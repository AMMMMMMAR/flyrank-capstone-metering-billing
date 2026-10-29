"""Run the cleanup job once, right now, with retries. Handy for a demo or a cron entry.

Run it with:  python -m scripts.run_cleanup
"""
import sys

from app.jobs.cleanup import cleanup_old_receipts
from app.jobs.runner import run_with_retries

if __name__ == "__main__":
    ok, deleted = run_with_retries("cleanup-old-receipts", cleanup_old_receipts)
    print(f"cleanup finished: ok={ok}, deleted={deleted}")
    sys.exit(0 if ok else 1)
