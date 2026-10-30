import argparse
import secrets
import sys

from sqlalchemy import inspect, select

from app import models
from app.database import SessionLocal, engine
from app.security import hash_api_key

PLANS = [
    {
        "id": "free",
        "name": "Free",
        "monthly_call_quota": 1000,
        "monthly_token_quota": 100_000,
        "base_price_cents": 0,
    },
    {
        "id": "pro",
        "name": "Pro",
        "monthly_call_quota": 50_000,
        "monthly_token_quota": 5_000_000,
        "base_price_cents": 2900,
    },
]

TENANTS = [
    ("Alpha Corp", "alpha@example.com", "free"),
    ("Beta LLC", "beta@example.com", "pro"),
]

# PUBLIC demo keys, only for demo databases:  python -m scripts.seed --demo-keys
# They let a reviewer copy and paste the commands from the README. Never use them for real data.
DEMO_KEYS = {
    "alpha@example.com": "mk_demo_alpha_free_0000",
    "beta@example.com": "mk_demo_beta_pro_0000",
}


def seed(demo_keys: bool = False) -> None:
    # The tables are built by the migrations, no longer by this script.
    if not inspect(engine).has_table("plans"):
        print("The database tables do not exist yet. Create them first with:  alembic upgrade head")
        sys.exit(1)

    with SessionLocal() as db:
        for plan in PLANS:
            if db.get(models.Plan, plan["id"]) is None:
                db.add(models.Plan(**plan))
                print(f"created plan: {plan['id']}")
            else:
                print(f"plan already exists: {plan['id']}")
        db.commit()

        for name, email, plan_id in TENANTS:
            existing = db.scalar(
                select(models.Tenant).where(models.Tenant.email == email)
            )
            if existing is not None:
                print(f"tenant already exists: {name} (its key was shown the first time)")
                continue

            if demo_keys:
                raw_key = DEMO_KEYS[email]
            else:
                raw_key = "mk_" + secrets.token_urlsafe(24)
            db.add(
                models.Tenant(
                    name=name,
                    email=email,
                    plan_id=plan_id,
                    api_key_hash=hash_api_key(raw_key),
                )
            )
            print(f"created tenant: {name} ({plan_id})  API KEY: {raw_key}")
        db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the starter plans and two demo tenants.")
    parser.add_argument(
        "--demo-keys",
        action="store_true",
        help="use the fixed PUBLIC demo API keys (demo databases only)",
    )
    seed(demo_keys=parser.parse_args().demo_keys)
