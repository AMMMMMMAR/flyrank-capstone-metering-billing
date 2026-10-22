import secrets

from sqlalchemy import select

from app import models
from app.database import Base, SessionLocal, engine
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


def seed() -> None:
    Base.metadata.create_all(bind=engine)

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
    seed()
