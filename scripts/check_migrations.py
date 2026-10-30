"""Proof for the 'schema as migrations' requirement.

Uses a temporary database (migrations_check.db), not your real metering.db.
Run it with:  python -m scripts.check_migrations
"""
import contextlib
import io
import os
import pathlib
import sys

# The temporary database must be chosen BEFORE the app is imported.
DB_FILE = pathlib.Path("migrations_check.db")
if DB_FILE.exists():
    DB_FILE.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///./{DB_FILE.name}"

from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app import models  # noqa: E402,F401
from app.database import Base, engine  # noqa: E402
from scripts.seed import seed  # noqa: E402

EXPECTED_TABLES = {
    "plans",
    "tenants",
    "usage_events",
    "idempotency_records",
    "processed_webhook_events",
    "job_alerts",
}
EXPECTED_INDEXES = {
    "idx_usage_tenant_recorded",
    "idx_idempotency_created",
    "idx_webhook_tenant_created",
}

USAGE = (
    "INSERT INTO usage_events (tenant_id, idempotency_key, calls, fresh_input_tokens, "
    "cached_input_tokens, output_tokens, reasoning_tokens, recorded_at) "
    "VALUES (:t, :k, :c, 0, 0, 0, 0, '2026-10-30 12:00:00')"
)
RECEIPT = (
    "INSERT INTO idempotency_records (tenant_id, key, request_hash, response_status, "
    "response_body, created_at) VALUES (:t, :k, 'h', 200, '{}', '2026-10-30 12:00:00')"
)
WEBHOOK = (
    "INSERT INTO processed_webhook_events (event_id, event_type, event_created, "
    "tenant_id, outcome, processed_at) "
    "VALUES (:e, 'x', 1, NULL, 'applied', '2026-10-30 12:00:00')"
)
TENANT = (
    "INSERT INTO tenants (name, email, api_key_hash, plan_id, subscription_status, "
    "created_at) VALUES ('X', :email, :h, 'free', 'active', '2026-10-30 12:00:00')"
)

cfg = Config("alembic.ini")
cfg.attributes["configure_logger"] = False  # keep the proof output clean
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS - {label}")
    else:
        print(f"FAIL - {label} {detail}")
        failures.append(label)


def alembic_run(action, *args):
    engine.dispose()  # let go of old connections before and after the schema changes
    action(cfg, *args)
    engine.dispose()


def table_names():
    engine.dispose()
    return set(inspect(engine).get_table_names())


def index_names():
    engine.dispose()
    inspector = inspect(engine)
    return {
        index["name"]
        for table in EXPECTED_TABLES
        for index in inspector.get_indexes(table)
    }


def drift():
    """Differences between the real database and models.py. An empty list means none."""
    engine.dispose()
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)


def current_revision():
    engine.dispose()
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def scalar(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def run_ok(sql, params=None):
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def refused(sql, params=None):
    """True if the database refused the statement. Nothing is ever saved by this helper."""
    with engine.connect() as conn:
        try:
            conn.execute(text(sql), params or {})
        except IntegrityError:
            conn.rollback()
            return True
        conn.rollback()
        return False


def run_seed():
    """Run the seed script, and return (exit code, what it printed). Keys stay out of the log."""
    buffer = io.StringIO()
    code = None
    with contextlib.redirect_stdout(buffer):
        try:
            seed()
        except SystemExit as exit_error:
            code = exit_error.code
    return code, buffer.getvalue()


def main():
    scripts = ScriptDirectory.from_config(cfg)
    head = scripts.get_current_head()

    print("--- Build a brand new database from the migrations ---")
    check("the migration history has exactly one head", len(scripts.get_heads()) == 1)
    alembic_run(command.upgrade, "head")
    check("all 6 tables were created (plus the alembic_version table)",
          table_names() == EXPECTED_TABLES | {"alembic_version"}, f"(got {table_names()})")
    check("the 3 indexes were created", EXPECTED_INDEXES <= index_names(),
          f"(got {index_names()})")
    check("the database is at the latest revision", current_revision() == head,
          f"(got {current_revision()})")
    differences = drift()
    check("the migrated schema has NO differences from models.py", differences == [],
          f"(differences: {differences})")

    print("--- The seed script works on a migrated database ---")
    code, first = run_seed()
    check("the first seed creates 2 plans and 2 tenants",
          code is None and first.count("created plan") == 2
          and first.count("created tenant") == 2)
    code, second = run_seed()
    check("the second seed creates nothing (safe to repeat)",
          code is None and "created" not in second and second.count("already exists") == 4)
    check("exactly 2 plans and 2 tenants exist",
          scalar("SELECT COUNT(*) FROM plans") == 2
          and scalar("SELECT COUNT(*) FROM tenants") == 2)

    print("--- The database rules exist in the MIGRATED schema ---")
    check("an event for a tenant that does not exist is refused (foreign key)",
          refused(USAGE, {"t": 9999, "k": "x", "c": 1}))
    check("negative calls are refused (check constraint)",
          refused(USAGE, {"t": 1, "k": "x", "c": -5}))
    run_ok(USAGE, {"t": 1, "k": "k1", "c": 1})
    check("a valid usage event is accepted", scalar("SELECT COUNT(*) FROM usage_events") == 1)
    check("the same (tenant, key) twice is refused (unique)",
          refused(USAGE, {"t": 1, "k": "k1", "c": 1}))
    check("the same key for ANOTHER tenant is accepted (keys belong to one tenant)",
          not refused(USAGE, {"t": 2, "k": "k1", "c": 1}))
    check("deleting a tenant that has usage events is refused (RESTRICT protects the ledger)",
          refused("DELETE FROM tenants WHERE id = :t", {"t": 1}))
    check("deleting a plan that tenants use is refused",
          refused("DELETE FROM plans WHERE id = 'free'"))
    check("a second tenant with the same email is refused (unique)",
          refused(TENANT, {"email": "alpha@example.com", "h": "hash-x"}))
    run_ok(RECEIPT, {"t": 2, "k": "r1"})
    check("the same receipt (tenant, key) twice is refused (unique)",
          refused(RECEIPT, {"t": 2, "k": "r1"}))
    run_ok(WEBHOOK, {"e": "evt_1"})
    check("the same Stripe event id twice is refused (primary key)",
          refused(WEBHOOK, {"e": "evt_1"}))
    run_ok("DELETE FROM tenants WHERE id = 2")
    check("deleting a tenant with no billing history also removes its receipts (CASCADE)",
          scalar("SELECT COUNT(*) FROM idempotency_records WHERE tenant_id = 2") == 0
          and scalar("SELECT COUNT(*) FROM tenants WHERE id = 2") == 0)

    print("--- Going back: downgrade to the empty database ---")
    alembic_run(command.downgrade, "base")
    check("downgrade removed every table (only alembic_version is left)",
          table_names() == {"alembic_version"}, f"(got {table_names()})")
    check("the database has no revision now", current_revision() is None)
    code, output = run_seed()
    check("seed refuses to run without tables, and says what to do",
          code == 1 and "alembic upgrade head" in output)

    print("--- An OLD database made by create_all, adopted with stamp ---")
    Base.metadata.create_all(bind=engine)  # exactly how the project used to build tables
    check("the old-style database has no revision yet", current_revision() is None)
    alembic_run(command.stamp, "head")
    check("after stamp, the database is at the latest revision", current_revision() == head)
    alembic_run(command.upgrade, "head")  # must do nothing and must not crash
    check("upgrade after stamp changes nothing",
          table_names() == EXPECTED_TABLES | {"alembic_version"})
    differences = drift()
    check("the stamped old database matches the models (stamp was safe)",
          differences == [], f"(differences: {differences})")

    print("--- Repeatable: down and up again ---")
    alembic_run(command.downgrade, "base")
    alembic_run(command.upgrade, "head")
    check("up again gives the full schema", table_names() == EXPECTED_TABLES | {"alembic_version"})
    differences = drift()
    check("and still no differences from models.py", differences == [],
          f"(differences: {differences})")


if __name__ == "__main__":
    try:
        main()
    finally:
        engine.dispose()
        try:
            DB_FILE.unlink(missing_ok=True)
        except PermissionError:
            print("(could not delete migrations_check.db; delete it by hand)")
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed.")
