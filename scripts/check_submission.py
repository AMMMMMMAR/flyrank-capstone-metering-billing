"""Dress rehearsal of the reviewer's run, on a clean setup.

1. It checks the submission pack (required files, tracked files, .env.example complete).
2. It starts the system with the documented run command on a TEMPORARY database and WITHOUT
   any Stripe keys (like a clean machine), seeds it with the documented seed command,
   and probes the live server over HTTP.

Run it with:  python -m scripts.check_submission
It never touches your real metering.db or your .env. If port 8765 is busy, set CHECK_PORT.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)

PORT = os.environ.get("CHECK_PORT", "8765")
BASE = f"http://127.0.0.1:{PORT}"
ALPHA = "mk_demo_alpha_free_0000"
BETA = "mk_demo_beta_pro_0000"

REQUIRED_FILES = [
    "README.md",
    "capstone.yaml",
    "EVIDENCE.md",
    "BUILDLOG.md",
    ".env.example",
    ".gitignore",
    "requirements.txt",
    "alembic.ini",
    "docs/design_doc.md",
]

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never use a proxy
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS - {label}")
    else:
        print(f"FAIL - {label} {detail}")
        failures.append(label)


def read(name: str) -> str:
    path = ROOT / name
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def tracked_files():
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return result.stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return None


def call(method, path, headers=None, body=None):
    """One HTTP call. Returns (status, lower-case headers, body text)."""
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE + path, data=data, headers=request_headers, method=method
    )
    try:
        with OPENER.open(request, timeout=10) as response:
            return (
                response.status,
                {k.lower(): v for k, v in response.headers.items()},
                response.read().decode("utf-8"),
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            {k.lower(): v for k, v in error.headers.items()},
            error.read().decode("utf-8"),
        )


def as_json(text: str) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def wait_for_health(server, seconds=45) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if server.poll() is not None:
            return False  # the server stopped by itself
        try:
            if call("GET", "/health")[0] == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def pack_checks():
    print("--- The submission pack ---")
    for name in REQUIRED_FILES:
        path = ROOT / name
        check(f"{name} exists and is not empty", path.is_file() and path.stat().st_size > 0)

    readme = read("README.md").lower()
    check("README has an architecture section", "## architecture" in readme)
    check("README documents the run command", "python -m scripts.start" in readme)
    check("README documents the seed command", "python -m scripts.seed" in readme)
    check("README has a limitations section", "## limitations" in readme)

    manifest = read("capstone.yaml")
    for key in ("run:", "seed:", "test:", "base_url:", "endpoints:"):
        check(f"capstone.yaml has {key}", key in manifest)

    evidence = read("EVIDENCE.md")
    check("EVIDENCE.md has no unfinished (todo) parts", "(todo)" not in evidence)
    check("EVIDENCE.md has the requirements checklist", "## Requirements checklist" in evidence)

    from app.config import Settings

    example = read(".env.example")
    missing = [n.upper() for n in Settings.model_fields if f"{n.upper()}=" not in example]
    check(".env.example lists every setting the app reads", not missing, f"(missing: {missing})")

    tracked = tracked_files()
    if tracked is None:
        print("SKIP - git is not available, so the tracked-files checks were skipped")
    else:
        check(".env is not tracked by git", ".env" not in tracked)
        check(".env.example is tracked by git", ".env.example" in tracked)
        check("no database file is tracked by git", not any(n.endswith(".db") for n in tracked))


def live_checks():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="metering_check_"))
    log_path = tmp / "server.log"
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": "sqlite:///" + (tmp / "check.db").as_posix(),
            "PORT": PORT,
            # Like a clean machine: no Stripe keys at all.
            "STRIPE_SECRET_KEY": "",
            "STRIPE_WEBHOOK_SECRET": "",
            "STRIPE_PRICE_ID": "",
        }
    )
    server = None
    log = open(log_path, "w")
    try:
        print("--- Start the system with the documented run command (temporary database, no Stripe keys) ---")
        server = subprocess.Popen(
            [sys.executable, "-m", "scripts.start"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        up = wait_for_health(server)
        check("the run command boots the system and /health answers", up)
        if not up:
            log.flush()
            print("   server log (last lines):")
            for line in log_path.read_text(errors="replace").splitlines()[-15:]:
                print("   " + line)
            print("   (if the port is busy, set CHECK_PORT to another number)")
            return

        seed = subprocess.run(
            [sys.executable, "-m", "scripts.seed", "--demo-keys"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        check("the seed command works on the fresh database",
              seed.returncode == 0 and "created tenant: Alpha Corp" in seed.stdout,
              f"(exit {seed.returncode}: {seed.stderr[-300:]})")

        print("--- Probe the live system ---")
        status, _, _ = call("GET", "/docs")
        check("the interactive docs page answers at /docs", status == 200)

        status, _, text = call("GET", "/me")
        check("GET /me without an API key returns 401", status == 401, f"(got {status})")
        status, _, text = call("GET", "/me", {"X-API-Key": "mk_wrong_key"})
        check("GET /me with a wrong key returns 401", status == 401, f"(got {status})")
        status, _, text = call("GET", "/me", {"X-API-Key": ALPHA})
        check("the demo Free tenant is known", status == 200 and as_json(text).get("plan") == "free",
              f"(got {status} {text})")
        status, _, text = call("GET", "/me", {"X-API-Key": BETA})
        check("the demo Pro tenant has the Pro limits",
              status == 200 and as_json(text).get("monthly_call_quota") == 50000,
              f"(got {status} {text})")

        body = {"fresh_input_tokens": 100, "output_tokens": 500}
        headers = {"X-API-Key": ALPHA, "Idempotency-Key": "rehearsal-1"}
        s1, h1, t1 = call("POST", "/generate", headers, body)
        s2, h2, t2 = call("POST", "/generate", headers, body)
        check("POST /generate returns 200", s1 == 200, f"(got {s1} {t1})")
        check("the same request again returns 200 with the IDENTICAL body", s2 == 200 and t1 == t2)
        check("the second answer is marked as a replay", h2.get("idempotent-replayed") == "true")
        check("the first answer is not marked as a replay", "idempotent-replayed" not in h1)

        status, _, text = call("GET", "/usage", {"X-API-Key": ALPHA})
        usage = as_json(text)
        check("GET /usage shows exactly 1 call (the retry was not counted)",
              status == 200 and usage.get("calls", {}).get("used") == 1, f"(got {text})")
        check("GET /usage shows the exact cost: 100 + 30 + 750 = 880 micro-dollars",
              usage.get("cost", {}).get("usage_micros") == 880, f"(got {usage.get('cost')})")

        status, _, _ = call("POST", "/generate", {"X-API-Key": ALPHA}, body)
        check("POST /generate without Idempotency-Key returns 400", status == 400, f"(got {status})")
        status, _, _ = call(
            "POST", "/generate", {"X-API-Key": ALPHA, "Idempotency-Key": "rehearsal-2"},
            {"output_tokens": -5},
        )
        check("a negative token count returns 422, never 500", status == 422, f"(got {status})")
        status, _, _ = call(
            "POST", "/generate", {"X-API-Key": ALPHA, "Idempotency-Key": "rehearsal-1"},
            {"output_tokens": 1},
        )
        check("the same key with a different body returns 422", status == 422, f"(got {status})")

        status, _, text = call(
            "POST", "/webhooks/stripe",
            {"Stripe-Signature": "t=1700000000,v1=0000forged"},
            {"id": "evt_forged", "type": "checkout.session.completed"},
        )
        check("a forged webhook returns 400", status == 400, f"(got {status} {text})")
        status, _, text = call("GET", "/me", {"X-API-Key": ALPHA})
        check("the forged webhook changed nothing", as_json(text).get("plan") == "free")

        status, _, _ = call("POST", "/checkout-session", {"X-API-Key": ALPHA})
        check("checkout says 'not configured' (503) when there are no Stripe keys", status == 503,
              f"(got {status})")
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    pack_checks()
    live_checks()


if __name__ == "__main__":
    main()
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed.")
