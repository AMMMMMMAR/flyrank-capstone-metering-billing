import logging
import threading
import time

from app.database import SessionLocal
from app.models import JobAlert

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 1.0  # seconds. The wait doubles after every failed attempt.

# The job logger has its own handler, so it prints even under uvicorn.
logger = logging.getLogger("jobs")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [jobs] %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def raise_alert(job_name: str, attempts: int, error: Exception) -> None:
    """The failure alert: a CRITICAL log line AND a row in the job_alerts table.

    The alert itself must never crash the worker, so every step is protected.
    """
    message = f"{type(error).__name__}: {error}"
    logger.critical(
        "ALERT: job %s failed after %d attempts: %s", job_name, attempts, message
    )
    try:
        with SessionLocal() as db:
            db.add(JobAlert(job_name=job_name, attempts=attempts, error=message[:1000]))
            db.commit()
    except Exception as alert_error:
        logger.critical("could not save the alert row: %s", alert_error)


def run_with_retries(
    job_name: str,
    func,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    sleep=time.sleep,
):
    """Run func(). If it fails, wait and try again (backoff). After the last failure, alert.

    Returns (succeeded, result). It never raises.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = func()
            logger.info("job %s succeeded on attempt %d: %s", job_name, attempt, result)
            return True, result
        except Exception as error:
            last_error = error
            logger.warning(
                "job %s failed on attempt %d of %d: %s", job_name, attempt, attempts, error
            )
            if attempt < attempts:
                sleep(base_delay * (2 ** (attempt - 1)))  # 1s, 2s, 4s ...

    raise_alert(job_name, attempts, last_error)
    return False, None


class PeriodicJob:
    """Runs a function again and again, in its OWN thread, every `interval_seconds`."""

    def __init__(self, name: str, func, interval_seconds: float):
        self.name = name
        self.func = func
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name=f"job-{self.name}", daemon=True
        )
        self._thread.start()
        logger.info("job %s started (every %s seconds)", self.name, self.interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()  # wakes the thread up at once if it is waiting
        if self._thread is not None:
            self._thread.join(timeout=10)
        logger.info("job %s stopped", self.name)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                run_with_retries(self.name, self.func)
            except Exception:  # run_with_retries never raises; this is the last safety net
                logger.exception("unexpected error in the loop of job %s", self.name)
