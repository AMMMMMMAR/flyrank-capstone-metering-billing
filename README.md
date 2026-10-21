# Usage Metering & Billing Engine

A backend service that meters customer usage, enforces plan quotas,
calculates cost, and syncs plans with Stripe (test mode).

Work in progress. Built stage by stage.

## Run

    venv\Scripts\activate
    uvicorn app.main:app --reload
