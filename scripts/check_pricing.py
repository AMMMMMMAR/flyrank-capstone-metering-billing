"""Proof for Probe 5: the pricing rules give exact totals, and GET /usage matches.

Uses a temporary database (pricing_check.db), not your real metering.db.
Run it with:  python -m scripts.check_pricing
"""
import os
import pathlib
import sys

# The temporary database must be chosen BEFORE the app is imported.
DB_FILE = pathlib.Path("pricing_check.db")
if DB_FILE.exists():
    DB_FILE.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///./{DB_FILE.name}"

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_api_key  # noqa: E402
from app.services.pricing import calculate_cost, format_usd  # noqa: E402
from scripts.seed import PLANS  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS - {label}")
    else:
        print(f"FAIL - {label} {detail}")
        failures.append(label)


def make_tenant(db, name, plan_id, key):
    db.add(
        models.Tenant(
            name=name,
            email=f"{name.replace(' ', '').lower()}@example.com",
            plan_id=plan_id,
            api_key_hash=hash_api_key(key),
        )
    )
    db.commit()


def post(client, key, idempotency_key, body):
    return client.post(
        "/generate",
        headers={"X-API-Key": key, "Idempotency-Key": idempotency_key},
        json=body,
    )


def get_usage(client, key):
    return client.get("/usage", headers={"X-API-Key": key})


def pricing_math():
    print("--- Pricing math (no database) ---")
    c = calculate_cost(calls=1, fresh=1000, cached=1000, output=1000, reasoning=1000)
    check("1000 fresh tokens cost 300", c["fresh_input_micros"] == 300, f"(got {c})")
    check("1000 cached tokens cost 75 (cheaper than fresh)", c["cached_input_micros"] == 75)
    check("1000 output + 1000 reasoning tokens cost 3000",
          c["output_and_reasoning_micros"] == 3000)
    check("1 call costs 100", c["calls_micros"] == 100)
    check("the usage total is 3475", c["usage_micros"] == 3475)

    reasoning_only = calculate_cost(calls=0, fresh=0, cached=0, output=0, reasoning=1000)
    output_only = calculate_cost(calls=0, fresh=0, cached=0, output=1000, reasoning=0)
    fresh_only = calculate_cost(calls=0, fresh=1000, cached=0, output=0, reasoning=0)
    check("reasoning tokens cost the same as output tokens (1500)",
          reasoning_only["usage_micros"] == output_only["usage_micros"] == 1500)
    check("reasoning tokens are NOT priced like fresh input",
          reasoning_only["usage_micros"] != fresh_only["usage_micros"])

    tiny = calculate_cost(calls=0, fresh=1, cached=1, output=1, reasoning=0)
    check("rounding goes UP: 1 + 1 + 2 = 4",
          (tiny["fresh_input_micros"], tiny["cached_input_micros"],
           tiny["output_and_reasoning_micros"], tiny["usage_micros"]) == (1, 1, 2, 4),
          f"(got {tiny})")

    zero = calculate_cost(calls=0, fresh=0, cached=0, output=0, reasoning=0)
    check("zero usage costs zero", zero["usage_micros"] == 0)

    check("format_usd(3475) is 0.003475", format_usd(3475) == "0.003475")
    check("format_usd(29000000) is 29.000000", format_usd(29_000_000) == "29.000000")
    check("format_usd(0) is 0.000000", format_usd(0) == "0.000000")

    try:
        calculate_cost(calls=-1, fresh=0, cached=0, output=0, reasoning=0)
        check("a negative number is refused", False)
    except ValueError:
        check("a negative number is refused", True)


def usage_endpoint():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for plan in PLANS:
            db.add(models.Plan(**plan))
        db.commit()
        make_tenant(db, "Price A", "free", "mk_price_a")
        make_tenant(db, "Price B", "free", "mk_price_b")
        make_tenant(db, "Price Pro", "pro", "mk_price_pro")

    with TestClient(app) as client:
        print("--- GET /usage (temporary database) ---")
        r = client.get("/usage")
        check("no API key returns 401", r.status_code == 401, f"(got {r.status_code})")

        big = {
            "fresh_input_tokens": 1000,
            "cached_input_tokens": 1000,
            "output_tokens": 1000,
            "reasoning_tokens": 1000,
        }
        r = post(client, "mk_price_a", "a-1", big)
        check("tenant A's request returns 200", r.status_code == 200)
        r = get_usage(client, "mk_price_a")
        check("GET /usage returns 200", r.status_code == 200, f"(got {r.status_code})")
        u = r.json()
        print("   Tenant A /usage:", u)
        check("calls used is 1", u["calls"]["used"] == 1)
        check("calls remaining is 999", u["calls"]["remaining"] == 999)
        check("tokens used is 4000", u["tokens"]["used"] == 4000)
        check("token breakdown is 1000 of each kind",
              (u["tokens"]["fresh_input"], u["tokens"]["cached_input"],
               u["tokens"]["output"], u["tokens"]["reasoning"]) == (1000, 1000, 1000, 1000))
        check("usage cost is 3475", u["cost"]["usage_micros"] == 3475)
        check("Free subscription fee is 0", u["cost"]["subscription_micros"] == 0)
        check("total_usd is 0.003475", u["cost"]["total_usd"] == "0.003475")

        for i in range(3):
            post(client, "mk_price_b", f"b-{i}", {"fresh_input_tokens": 1})
        ub = get_usage(client, "mk_price_b").json()
        print("   Tenant B /usage:", ub)
        check("3 events of 1 fresh token: rounded ONCE on the total (1, not 3)",
              ub["cost"]["fresh_input_micros"] == 1,
              f"(got {ub['cost']['fresh_input_micros']})")
        check("tenant B usage cost is 301 (300 for calls + 1)",
              ub["cost"]["usage_micros"] == 301)
        check("tenant A's numbers did not change (tenants are isolated)",
              get_usage(client, "mk_price_a").json()["tokens"]["used"] == 4000)

        up = get_usage(client, "mk_price_pro").json()
        print("   Pro tenant /usage:", up)
        check("Pro limit is 50000 calls", up["calls"]["limit"] == 50000)
        check("Pro subscription fee is 29000000 micro-dollars",
              up["cost"]["subscription_micros"] == 29_000_000)
        check("Pro total_usd is 29.000000", up["cost"]["total_usd"] == "29.000000")


if __name__ == "__main__":
    try:
        pricing_math()
        usage_endpoint()
    finally:
        engine.dispose()
        try:
            DB_FILE.unlink(missing_ok=True)
        except PermissionError:
            print("(could not delete pricing_check.db; delete it by hand)")
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed.")
