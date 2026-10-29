"""Proof for Probes 3 and 4: Checkout upgrades a tenant, and webhooks are safe.

Uses a temporary database (stripe_check.db). It NEVER calls Stripe: the checkout call is
replaced by a fake, and the webhook events are signed here the same way Stripe signs them.
Run it with:  python -m scripts.check_stripe
"""
import hashlib
import hmac
import json
import os
import pathlib
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

# The temporary database and the test secrets must be chosen BEFORE the app is imported.
DB_FILE = pathlib.Path("stripe_check.db")
if DB_FILE.exists():
    DB_FILE.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///./{DB_FILE.name}"
SECRET = "whsec_test_secret_for_the_check_script"
os.environ["STRIPE_WEBHOOK_SECRET"] = SECRET
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_for_the_check_script"
os.environ["STRIPE_PRICE_ID"] = "price_fake_for_the_check_script"

import stripe  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app import models  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_api_key  # noqa: E402
from scripts.seed import PLANS  # noqa: E402

failures = []
_counter = [0]


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


def sign(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header: HMAC-SHA256 of '<timestamp>.<raw body>'."""
    t = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        secret.encode(), f"{t}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={t},v1={digest}"


def make_event(event_type, obj, created=None):
    _counter[0] += 1
    return {
        "id": f"evt_test_{_counter[0]:04d}",
        "object": "event",
        "type": event_type,
        "created": int(time.time()) if created is None else created,
        "data": {"object": obj},
    }


def checkout_completed(tenant_id, suffix):
    return {
        "object": "checkout.session",
        "id": f"cs_test_{suffix}",
        "mode": "subscription",
        "payment_status": "paid",
        "client_reference_id": str(tenant_id),
        "customer": f"cus_test_{suffix}",
        "subscription": f"sub_test_{suffix}",
    }


def subscription(suffix, tenant_id, status):
    return {
        "object": "subscription",
        "id": f"sub_test_{suffix}",
        "customer": f"cus_test_{suffix}",
        "status": status,
        "metadata": {"tenant_id": str(tenant_id)},
    }


def post_webhook(client, payload: bytes, signature):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return client.post("/webhooks/stripe", content=payload, headers=headers)


def deliver(client, event):
    """A properly signed delivery, like the real Stripe would send."""
    payload = json.dumps(event).encode()
    return post_webhook(client, payload, sign(payload))


def gen(client, key, idempotency_key):
    return client.post(
        "/generate",
        headers={"X-API-Key": key, "Idempotency-Key": idempotency_key},
        json={},
    )


def usage(client, key):
    return client.get("/usage", headers={"X-API-Key": key}).json()


def tenant_state(tenant_id):
    with SessionLocal() as db:
        t = db.get(models.Tenant, tenant_id)
        return t.plan_id, t.subscription_status, t.stripe_subscription_id


def count_rows(event_id):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(models.ProcessedWebhookEvent)
            .where(models.ProcessedWebhookEvent.event_id == event_id)
        )


def main():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for plan in PLANS:
            db.add(models.Plan(**plan))
        db.commit()
        up_id = make_tenant(db, "Up Full", "free", "mk_up_full")
        forge_id = make_tenant(db, "Forge", "free", "mk_forge")
        replay_id = make_tenant(db, "Replay", "free", "mk_replay")
        order_id = make_tenant(db, "Order", "free", "mk_order")
        db.add_all(  # the "Up Full" tenant has already used all 1000 Free calls
            [
                models.UsageEvent(tenant_id=up_id, idempotency_key=f"seed-{i}")
                for i in range(1000)
            ]
        )
        db.commit()

    with TestClient(app) as client:
        print("--- Probe 3: Checkout flips the tenant Free -> Pro ---")
        r = client.post("/checkout-session")
        check("checkout without an API key returns 401", r.status_code == 401)

        fake_session = SimpleNamespace(
            url="https://checkout.stripe.test/pay/abc", id="cs_test_fake"
        )
        with patch.object(
            stripe.checkout.Session, "create", return_value=fake_session
        ) as create:
            r = client.post("/checkout-session", headers={"X-API-Key": "mk_up_full"})
        check("checkout returns 200 and a payment url",
              r.status_code == 200 and r.json().get("checkout_url", "").startswith("https://"),
              f"(got {r.status_code} {r.text})")
        sent = create.call_args.kwargs if create.call_args else {}
        check("the session asks for a subscription", sent.get("mode") == "subscription")
        check("the tenant id travels in client_reference_id",
              sent.get("client_reference_id") == str(up_id))
        check("the tenant id is also copied onto the subscription metadata",
              sent.get("subscription_data") == {"metadata": {"tenant_id": str(up_id)}})

        saved_price = settings.stripe_price_id
        settings.stripe_price_id = ""
        r = client.post("/checkout-session", headers={"X-API-Key": "mk_up_full"})
        settings.stripe_price_id = saved_price
        check("without a price id configured, checkout returns 503", r.status_code == 503)

        r = gen(client, "mk_up_full", "before-upgrade")
        check("before the upgrade the Free tenant is blocked (429)", r.status_code == 429)
        check("/usage shows the Free limit (1000 calls)",
              usage(client, "mk_up_full")["calls"]["limit"] == 1000)

        event = make_event("checkout.session.completed", checkout_completed(up_id, "up"))
        r = deliver(client, event)
        print("   webhook answer:", r.json())
        check("the signed checkout.session.completed returns 200", r.status_code == 200,
              f"(got {r.status_code} {r.text})")
        check("its outcome is applied", r.json().get("outcome") == "applied")
        check("the tenant is now on the pro plan",
              tenant_state(up_id)[:2] == ("pro", "active"), f"(got {tenant_state(up_id)})")
        check("the Stripe subscription id was saved",
              tenant_state(up_id)[2] == "sub_test_up")
        u = usage(client, "mk_up_full")
        check("/usage shows the Pro plan", u["plan"] == "pro")
        check("/usage shows the Pro limit (50000 calls)", u["calls"]["limit"] == 50000)
        check("/usage shows the 29.00 subscription fee",
              u["cost"]["subscription_micros"] == 29_000_000)
        r = gen(client, "mk_up_full", "after-upgrade")
        check("the same tenant can now make calls (200)", r.status_code == 200,
              f"(got {r.status_code})")
        r = client.post("/checkout-session", headers={"X-API-Key": "mk_up_full"})
        check("a Pro tenant asking for checkout again gets 409", r.status_code == 409)

        print("--- Probe 4: forged and replayed webhooks ---")
        forge_event = make_event(
            "checkout.session.completed", checkout_completed(forge_id, "forge")
        )
        payload = json.dumps(forge_event).encode()

        r = post_webhook(client, payload, sign(payload, secret="whsec_wrong_secret"))
        check("a forged signature returns 400", r.status_code == 400, f"(got {r.status_code})")
        check("nothing changed after the forged event",
              tenant_state(forge_id)[:2] == ("free", "active")
              and count_rows(forge_event["id"]) == 0)

        r = post_webhook(client, payload, None)
        check("a missing signature header returns 400", r.status_code == 400)

        old_marker = b'"client_reference_id": "%d"' % forge_id
        new_marker = b'"client_reference_id": "%d"' % order_id
        tampered = payload.replace(old_marker, new_marker)
        check("(test setup) the tampered body really is different", tampered != payload)
        r = post_webhook(client, tampered, sign(payload))
        check("a body changed after signing returns 400", r.status_code == 400)
        check("the tampered event upgraded nobody",
              tenant_state(forge_id)[0] == "free" and tenant_state(order_id)[0] == "free")

        r = post_webhook(client, payload, sign(payload, timestamp=int(time.time()) - 3600))
        check("a correctly signed but 1-hour-old delivery returns 400", r.status_code == 400)

        r = post_webhook(client, b"not json at all", sign(b"not json at all"))
        check("a signed body that is not JSON returns 400, never 500", r.status_code == 400)

        replay_event = make_event(
            "checkout.session.completed", checkout_completed(replay_id, "replay")
        )
        r1 = deliver(client, replay_event)
        check("first delivery returns 200 and is applied",
              r1.status_code == 200 and r1.json().get("outcome") == "applied")
        check("the tenant is Pro after the first delivery", tenant_state(replay_id)[0] == "pro")
        with SessionLocal() as db:  # undo the upgrade by hand, to prove a replay is NOT re-applied
            tenant = db.get(models.Tenant, replay_id)
            tenant.plan_id = "free"
            db.commit()
        r2 = deliver(client, replay_event)
        print("   replay answer:", r2.json())
        check("replaying the same event id returns 200", r2.status_code == 200)
        check("the replay is reported as a duplicate", r2.json().get("outcome") == "duplicate")
        check("the replay changed nothing (tenant is still free)",
              tenant_state(replay_id)[0] == "free")
        check("the event is stored exactly once", count_rows(replay_event["id"]) == 1)

        print("--- Events that must not break anything ---")
        r = deliver(client, make_event("checkout.session.completed", checkout_completed(9999, "ghost")))
        check("an event for an unknown tenant returns 200 and is ignored",
              r.status_code == 200 and r.json().get("outcome") == "ignored")
        r = deliver(client, make_event("invoice.paid", {"object": "invoice", "id": "in_test"}))
        check("an event type we do not use returns 200 and is ignored",
              r.status_code == 200 and r.json().get("outcome") == "ignored")
        old_sub = make_event(
            "customer.subscription.deleted",
            {"object": "subscription", "id": "sub_test_old", "customer": "cus_test_up",
             "status": "canceled", "metadata": {"tenant_id": str(up_id)}},
        )
        r = deliver(client, old_sub)
        check("news about an OLDER subscription does not downgrade the tenant",
              r.json().get("outcome") == "ignored" and tenant_state(up_id)[0] == "pro")

        print("--- Events arriving out of order ---")
        t0 = int(time.time()) - 1000

        def sub_event(status, created, kind="customer.subscription.updated"):
            return make_event(kind, subscription("order", order_id, status), created=created)

        r = deliver(client, make_event(
            "checkout.session.completed", checkout_completed(order_id, "order"), created=t0))
        check("E1 checkout completed is applied (plan pro)",
              r.json().get("outcome") == "applied" and tenant_state(order_id)[0] == "pro")
        r = deliver(client, sub_event("past_due", t0 + 20))
        check("E2 past_due is applied", r.json().get("outcome") == "applied")
        check("a past_due tenant is blocked with 402",
              gen(client, "mk_order", "order-1").status_code == 402)
        r = deliver(client, sub_event("active", t0 + 10))  # OLDER than E2, arrives late
        check("E3 an older event arriving late is ignored (stale_ignored)",
              r.json().get("outcome") == "stale_ignored", f"(got {r.json()})")
        check("the tenant is still past_due (402)",
              gen(client, "mk_order", "order-2").status_code == 402)
        r = deliver(client, sub_event("active", t0 + 30))
        check("E4 active is applied", r.json().get("outcome") == "applied")
        check("the tenant can use the API again (200)",
              gen(client, "mk_order", "order-3").status_code == 200)
        r = deliver(client, sub_event("canceled", t0 + 40, kind="customer.subscription.deleted"))
        check("E5 subscription deleted is applied", r.json().get("outcome") == "applied")
        check("the tenant is back on the free plan",
              tenant_state(order_id)[:2] == ("free", "active"))
        check("the subscription id was cleared", tenant_state(order_id)[2] is None)
        r = deliver(client, sub_event("active", t0 + 35))  # OLDER than E5
        check("E6 an older 'active' event cannot bring Pro back",
              r.json().get("outcome") == "stale_ignored" and tenant_state(order_id)[0] == "free")


if __name__ == "__main__":
    try:
        main()
    finally:
        engine.dispose()
        try:
            DB_FILE.unlink(missing_ok=True)
        except PermissionError:
            print("(could not delete stripe_check.db; delete it by hand)")
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed.")
