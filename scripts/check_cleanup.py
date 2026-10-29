"""Proof for the background-job requirement: it runs off the request path, retries with
backoff, and raises a failure alert. Also proves that an expired idempotency key is refused.

Uses a temporary database (cleanup_check.db), not your real metering.db.
Run it with:  python -m scripts.check_cleanup
"""
import os
import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# The temporary database must be chosen BEFORE the app is imported.
DB_FILE = pathlib.Path("cleanup_check.db")
if DB_FILE.exists():
    DB_FILE.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///./{DB_FILE.name}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select, update  # noqa: E402

from app import models  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.jobs.cleanup import cleanup_old_receipts  # noqa: E402
from app.jobs.runner import PeriodicJob, run_with_retries  # noqa: E402
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


def post(client, key, idempotency_key, body):
    return client.post(
        "/generate",
        headers={"X-API-Key": key, "Idempotency-Key": idempotency_key},
        json=body,
    )


def count(model, *conditions):
    stmt = select(func.count()).select_from(model)
    for condition in conditions:
        stmt = stmt.where(condition)
    with SessionLocal() as db:
        return db.scalar(stmt)


def make_flaky(failures_before_success: int):
    """A fake job that fails N times, then works."""
    state = {"calls": 0}

    def job():
        state["calls"] += 1
        if state["calls"] <= failures_before_success:
            raise RuntimeError(f"simulated failure {state['calls']}")
        return "done"

    return job, state


def part_cleanup():
    print("--- The cleanup job deletes only OLD receipts ---")
    with SessionLocal() as db:
        for plan in PLANS:
            db.add(models.Plan(**plan))
        db.commit()
        tenant_id = make_tenant(db, "Clean A", "free", "mk_clean_a")

    with TestClient(app) as client:
        r_old = post(client, "mk_clean_a", "old-key", {"output_tokens": 10})
        r_new = post(client, "mk_clean_a", "new-key", {"output_tokens": 20})
        check("two requests were accepted", r_old.status_code == 200 and r_new.status_code == 200)

        long_ago = datetime.now(timezone.utc) - timedelta(hours=48)
        with SessionLocal() as db:
            db.execute(
                update(models.IdempotencyRecord)
                .where(models.IdempotencyRecord.key == "old-key")
                .values(created_at=long_ago)
            )
            db.add_all(
                [
                    models.IdempotencyRecord(
                        tenant_id=tenant_id, key=f"bulk-{i}", request_hash="x",
                        response_status=200, response_body="{}", created_at=long_ago,
                    )
                    for i in range(1200)
                ]
            )
            db.commit()
        check("1202 receipts exist before the cleanup", count(models.IdempotencyRecord) == 1202)

        deleted = cleanup_old_receipts()
        check("the job deleted the 1201 old receipts (in several batches)", deleted == 1201,
              f"(got {deleted})")
        check("only the recent receipt is left", count(models.IdempotencyRecord) == 1)
        check("no usage event was touched (billing history is safe)",
              count(models.UsageEvent) == 2)
        check("running the job again deletes nothing (safe to repeat)",
              cleanup_old_receipts() == 0)

        r = post(client, "mk_clean_a", "new-key", {"output_tokens": 20})
        check("a recent key can still be replayed",
              r.status_code == 200 and r.headers.get("idempotent-replayed") == "true")
        r = post(client, "mk_clean_a", "old-key", {"output_tokens": 10})
        check("an expired key is refused with 409 (never a 500)", r.status_code == 409,
              f"(got {r.status_code} {r.text})")
        print("   409 answer:", r.json())
        check("the expired key was NOT counted a second time", count(models.UsageEvent) == 2)


def part_retries():
    print("--- Retries with backoff, and the failure alert ---")
    delays = []

    job, state = make_flaky(0)
    ok, result = run_with_retries("t-first", job, sleep=delays.append)
    check("a job that works the first time runs once and does not wait",
          ok and state["calls"] == 1 and delays == [])

    job, state = make_flaky(2)
    ok, result = run_with_retries("t-second", job, sleep=delays.append)
    check("a job that fails twice and then works is retried and succeeds",
          ok and result == "done" and state["calls"] == 3)
    check("the waits grow: 1 second, then 2 seconds (backoff)", delays == [1.0, 2.0],
          f"(got {delays})")
    check("no alert was raised, because it finally worked", count(models.JobAlert) == 0)

    delays.clear()
    job, state = make_flaky(99)
    ok, result = run_with_retries("t-broken", job, sleep=delays.append)
    check("a job that always fails stops after 3 attempts", (not ok) and state["calls"] == 3)
    check("it waited only BETWEEN attempts (1s, 2s)", delays == [1.0, 2.0], f"(got {delays})")
    check("exactly one failure alert was saved", count(models.JobAlert) == 1)
    with SessionLocal() as db:
        alert = db.scalar(select(models.JobAlert))
        print("   alert row:", alert.job_name, alert.attempts, alert.error)
        check("the alert names the job, the attempts and the error",
              alert.job_name == "t-broken" and alert.attempts == 3
              and "simulated failure 3" in alert.error)

    job, state = make_flaky(99)
    with patch("app.jobs.runner.SessionLocal", side_effect=RuntimeError("database is down")):
        try:
            ok, _ = run_with_retries("t-nodb", job, sleep=lambda seconds: None)
            survived = not ok
        except Exception:
            survived = False
    check("if the alert itself cannot be saved, the worker still does not crash", survived)


def part_scheduler(client):
    print("--- The job runs on a schedule, off the request path ---")
    thread_names = []

    def quick():
        thread_names.append(threading.current_thread().name)
        return "tick"

    quick_job = PeriodicJob("t-quick", quick, interval_seconds=0.05)
    quick_job.start()
    time.sleep(0.6)
    quick_job.stop()
    check("the job ran several times by itself", len(thread_names) >= 3,
          f"(got {len(thread_names)})")
    check("it ran in its own thread, not in a request thread",
          all(name == "job-t-quick" for name in thread_names))
    runs_at_stop = len(thread_names)
    time.sleep(0.3)
    check("after stop() it does not run any more", len(thread_names) == runs_at_stop)

    def slow():
        time.sleep(1.0)
        return "slow done"

    slow_job = PeriodicJob("t-slow", slow, interval_seconds=0.01)
    slow_job.start()
    time.sleep(0.2)  # now the slow job is busy for about one second
    started = time.perf_counter()
    r = client.get("/health")
    elapsed = time.perf_counter() - started
    slow_job.stop()
    check("while a slow job is running, the API still answers fast",
          r.status_code == 200 and elapsed < 0.5, f"(took {elapsed:.2f}s)")


def part_lifespan():
    print("--- The job starts and stops with the application ---")
    with TestClient(app):
        job = app.state.cleanup_job
        check("the cleanup job is started with the application",
              job is not None and job.is_alive())
    check("it is stopped when the application stops", not job.is_alive())

    settings.background_jobs_enabled = False
    try:
        with TestClient(app):
            check("with BACKGROUND_JOBS_ENABLED=false no job is started",
                  app.state.cleanup_job is None)
    finally:
        settings.background_jobs_enabled = True


def main():
    Base.metadata.create_all(bind=engine)
    part_cleanup()
    part_retries()
    with TestClient(app) as client:
        part_scheduler(client)
    part_lifespan()


if __name__ == "__main__":
    try:
        main()
    finally:
        engine.dispose()
        try:
            DB_FILE.unlink(missing_ok=True)
        except PermissionError:
            print("(could not delete cleanup_check.db; delete it by hand)")
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed.")
