from fastapi import FastAPI

app = FastAPI(title="Usage Metering & Billing Engine")


@app.get("/health")
def health():
    return {"status": "ok"}
