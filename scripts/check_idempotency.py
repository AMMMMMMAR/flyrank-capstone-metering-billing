"""Proof for Probe 1: the same request twice counts once.

Uses a temporary database (idem_check.db), not your real metering.db.
Run it with:  python -m scripts.check_idempotency
"""
import os
import pathlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

# The temporary database must be chosen BEFORE the app is imported.
DB_FILE = pathlib.Path("idem_check.db")
if DB_FILE.exists():
    DB_FILE.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///./{DB_FILE.name}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_api_key  # noqa: E402
from scripts.seed import PLANS  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS - {label}")
    else:
        print(f"FAIL - {label} {detail}")
        failures.append(label)


def make_tenant(db, name, plan_id, key):
    tenant = models.Tenant(
        name=name,
        email=f"{name.replace(' ', '').lower()}@example.com",
        plan_id=plan_id,
        api_key_hash=hash_api_key(key),
    )
    db.add(tenant)
    db.commit()
    return tenant.id


def post(client, key, idempotency_key, body=None):
    return client.post(
        "/generate",
        headers={"X-API-Key": key, "Idempotency-Key": idempotency_key},
        json=body or {},
    )


def count_events(tenant_id):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(models.UsageEvent)
            .where(models.UsageEvent.tenant_id == tenant_id)
        )


def count_records(tenant_id):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(models.IdempotencyRecord)
            .where(models.IdempotencyRecord.tenant_id == tenant_id)
        )


def main():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for plan in PLANS:
            db.add(models.Plan(**plan))
        db.commit()
        a_id = make_tenant(db, "Idem A", "free", "mk_idem_a")
        b_id = make_tenant(db, "Idem B", "free", "mk_idem_b")
        full_id = make_tenant(db, "Idem Full", "free", "mk_idem_full")
        race_id = make_tenant(db, "Idem Race", "free", "mk_idem_race")
        db.add_all(
            [
                models.UsageEvent(tenant_id=full_id, idempotency_key=f"seed-{i}")
                for i in range(999)
            ]
        )
        db.commit()

    body = {"fresh_input_tokens": 100, "output_tokens": 500}

    with TestClient(app) as client:
        print("--- Same request twice (same key, same body) ---")
        r1 = post(client, "mk_idem_a", "key-1", body)
        r2 = post(client, "mk_idem_a", "key-1", body)
        check("first request returns 200", r1.status_code == 200, f"(got {r1.status_code})")
        check("second request returns 200", r2.status_code == 200, f"(got {r2.status_code})")
        check("second response is byte-for-byte the first", r1.content == r2.content)
        check("first response is not marked as replayed",
              "idempotent-replayed" not in r1.headers)
        check("second response is marked as replayed",
              r2.headers.get("idempotent-replayed") == "true")
        check("exactly 1 usage event stored", count_events(a_id) == 1,
              f"(got {count_events(a_id)})")
        print("   first :", r1.json())
        print("   second:", r2.json())

        print("--- Same key, different body ---")
        r3 = post(client, "mk_idem_a", "key-1", {"fresh_input_tokens": 999})
        check("returns 422", r3.status_code == 422, f"(got {r3.status_code})")
        check("error says idempotency_key_reused",
              r3.status_code == 422 and r3.json()["detail"]["error"] == "idempotency_key_reused")
        check("still exactly 1 usage event", count_events(a_id) == 1)

        print("--- Same key text, different tenant ---")
        r4 = post(client, "mk_idem_b", "key-1", body)
        check("tenant B gets its own 200", r4.status_code == 200, f"(got {r4.status_code})")
        check("tenant B's event belongs to tenant B", r4.json()["tenant_id"] == b_id)
        check("one event each (A and B)", count_events(a_id) == 1 and count_events(b_id) == 1)

        print("--- Retry after the quota is used up (999 of 1000 calls used) ---")
        last = post(client, "mk_idem_full", "last-call")
        check("the 1000th call returns 200", last.status_code == 200, f"(got {last.status_code})")
        nxt = post(client, "mk_idem_full", "next-call")
        check("a NEW key now returns 429", nxt.status_code == 429, f"(got {nxt.status_code})")
        check("the rejected request did not use up its key", count_records(full_id) == 1,
              f"(got {count_records(full_id)})")
        replay = post(client, "mk_idem_full", "last-call")
        check("retrying the 1000th call returns 200, not 429", replay.status_code == 200,
              f"(got {replay.status_code})")
        check("the retry returns the original answer", replay.content == last.content)
        check("still exactly 1000 events", count_events(full_id) == 1000,
              f"(got {count_events(full_id)})")

    print("--- Race: 10 identical requests at the same moment ---")
    barrier = threading.Barrier(10)

    def worker(_):
        with TestClient(app) as local_client:
            barrier.wait(timeout=60)  # all 10 threads fire together
            return post(local_client, "mk_idem_race", "race-key", body)

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(worker, range(10)))

    codes = sorted(r.status_code for r in responses)
    replayed = sum(
        1 for r in responses if r.headers.get("idempotent-replayed") == "true"
    )
    print("   status codes:", codes)
    print("   replayed answers:", replayed)
    check("all 10 requests returned 200", codes == [200] * 10)
    check("only 1 usage event was created", count_events(race_id) == 1,
          f"(got {count_events(race_id)})")
    check("all 10 answers are identical", len({r.content for r in responses}) == 1)
    check("exactly 9 answers were replays", replayed == 9)


if __name__ == "__main__":
    try:
        main()
    finally:
        engine.dispose()
        try:
            DB_FILE.unlink(missing_ok=True)
        except PermissionError:
            print("(could not delete idem_check.db; delete it by hand)")
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed.")
