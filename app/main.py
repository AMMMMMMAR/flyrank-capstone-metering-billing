from fastapi import FastAPI

from app.routers import generate, me

app = FastAPI(title="Usage Metering & Billing Engine")

app.include_router(me.router)
app.include_router(generate.router)


@app.get("/health")
def health():
    return {"status": "ok"}
