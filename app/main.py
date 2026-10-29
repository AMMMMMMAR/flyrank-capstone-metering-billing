from fastapi import FastAPI

from app.routers import billing, generate, me, usage, webhooks

app = FastAPI(title="Usage Metering & Billing Engine")

app.include_router(me.router)
app.include_router(generate.router)
app.include_router(usage.router)
app.include_router(billing.router)
app.include_router(webhooks.router)


@app.get("/health")
def health():
    return {"status": "ok"}
