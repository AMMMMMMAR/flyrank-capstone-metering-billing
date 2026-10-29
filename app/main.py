from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.jobs.cleanup import cleanup_old_receipts
from app.jobs.runner import PeriodicJob
from app.routers import billing, generate, me, usage, webhooks


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background job when the app starts, stop it when the app stops."""
    app.state.cleanup_job = None
    if settings.background_jobs_enabled:
        job = PeriodicJob(
            "cleanup-old-receipts", cleanup_old_receipts, settings.cleanup_interval_seconds
        )
        job.start()
        app.state.cleanup_job = job
    try:
        yield
    finally:
        if app.state.cleanup_job is not None:
            app.state.cleanup_job.stop()


app = FastAPI(title="Usage Metering & Billing Engine", lifespan=lifespan)

app.include_router(me.router)
app.include_router(generate.router)
app.include_router(usage.router)
app.include_router(billing.router)
app.include_router(webhooks.router)


@app.get("/health")
def health():
    return {"status": "ok"}
