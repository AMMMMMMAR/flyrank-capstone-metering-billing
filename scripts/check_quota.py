"""Proof for Probe 2: exact quota boundary, token boundary, 402, and the race.

This script uses a temporary database (quota_check.db), not your real metering.db.
Run it with:  python -m scripts.check_quota
"""
import os
import pathlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

# The temporary database must be chosen BEFORE the app is imported.
DB_FILE = pathlib.Path("quota_check.db")
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


def make_tenant(db, name, plan_id, key, status="active"):
    tenant = models.Tenant(
        name=name,
        email=f"{name.replace(' ', '').lower()}@example.com",
        plan_id=plan_id,
        api_key_hash=hash_api_key(key),
        subscription_status=status,
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


def main():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for plan in PLANS:
            db.add(models.Plan(**plan))
        db.commit()
        calls_id = make_tenant(db, "Calls Test", "free", "mk_calls_test")
        make_tenant(db, "Tokens Test", "free", "mk_tokens_test")
        late_id = make_tenant(db, "Late Payer", "pro", "mk_late_test", status="past_due")
        race_id = make_tenant(db, "Race Test", "free", "mk_race_test")

    with TestClient(app) as client:
        print("--- Call boundary (Free plan = 1000 calls per month) ---")
        statuses = [
            post(client, "mk_calls_test", f"call-{i}").status_code
            for i in range(1, 1001)
        ]
        check("calls 1 to 1000 all return 200", all(s == 200 for s in statuses),
              f"(got {set(statuses)})")

        r = post(client, "mk_calls_test", "call-1001")
        check("call 1001 returns 429", r.status_code == 429, f"(got {r.status_code})")
        retry_after = r.headers.get("retry-after")
        check("429 has a Retry-After header", retry_after is not None and int(retry_after) > 0)
        print("   Retry-After:", retry_after, "seconds")
        print("   body:", r.json())
        check("exactly 1000 events are stored (the 1001st saved nothing)",
              count_events(calls_id) == 1000, f"(got {count_events(calls_id)})")

        print("--- Token boundary (Free plan = 100000 tokens per month) ---")
        r = post(client, "mk_tokens_test", "tok-1", {"output_tokens": 100_000})
        check("a request landing exactly on the token limit returns 200",
              r.status_code == 200, f"(got {r.status_code})")
        r = post(client, "mk_tokens_test", "tok-2", {"output_tokens": 1})
        check("one more token returns 429", r.status_code == 429, f"(got {r.status_code})")
        check("the error names the tokens quota",
              r.status_code == 429 and r.json()["detail"]["quota"] == "tokens")

        print("--- Payment lapsed (Pro plan, past_due) ---")
        r = post(client, "mk_late_test", "late-1")
        check("past_due tenant returns 402", r.status_code == 402, f"(got {r.status_code})")
        check("the 402 saved nothing", count_events(late_id) == 0)

    print("--- Race: 10 simultaneous requests when 999 of 1000 calls are used ---")
    with SessionLocal() as db:
        db.add_all(
            [
                models.UsageEvent(tenant_id=race_id, idempotency_key=f"seed-{i}")
                for i in range(999)
            ]
        )
        db.commit()

    barrier = threading.Barrier(10)

    def worker(n):
        with TestClient(app) as local_client:
            barrier.wait(timeout=60)  # all 10 threads fire together
            return post(local_client, "mk_race_test", f"race-{n}").status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(worker, range(10)))

    print("   status codes:", sorted(results))
    check("exactly one request got 200", results.count(200) == 1)
    check("the other nine got 429", results.count(429) == 9)
    check("exactly 1000 events stored (never 1001 or more)",
          count_events(race_id) == 1000, f"(got {count_events(race_id)})")


if __name__ == "__main__":
    try:
        main()
    finally:
        engine.dispose()
        try:
            DB_FILE.unlink(missing_ok=True)
        except PermissionError:
            print("(could not delete quota_check.db; delete it by hand)")
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed.")
