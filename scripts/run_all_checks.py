"""Run every proof script, one after another, and print a short summary.

Run it with:  python -m scripts.run_all_checks
It takes about one to two minutes. The exit code is 0 only if every check passed.
Each script uses its own temporary database, so your real data is never touched.
"""
import subprocess
import sys
import time

CHECKS = [
    ("Schema built from the migrations", "scripts.check_migrations"),
    ("Quota boundary, 429 and 402 (Probe 2)", "scripts.check_quota"),
    ("Idempotent metering (Probe 1)", "scripts.check_idempotency"),
    ("Pricing rules and /usage (Probe 5)", "scripts.check_pricing"),
    ("Stripe checkout and webhooks (Probes 3, 4)", "scripts.check_stripe"),
    ("Background job: retries and alert", "scripts.check_cleanup"),
]


def run_one(module: str):
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", module],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    seconds = time.perf_counter() - started
    lines = proc.stdout.splitlines()
    passed = sum(1 for line in lines if line.startswith("PASS - "))
    failed_lines = [line for line in lines if line.startswith("FAIL - ")]
    ok = proc.returncode == 0 and not failed_lines and passed > 0
    return ok, passed, failed_lines, seconds, proc


def main() -> int:
    print(f"Running {len(CHECKS)} proof scripts, one after another...", flush=True)
    total_passed = 0
    problems = []
    for title, module in CHECKS:
        ok, passed, failed_lines, seconds, proc = run_one(module)
        total_passed += passed
        status = "OK  " if ok else "FAIL"
        print(f"{status} {title:<44} {passed:>3} checks passed  {seconds:5.1f}s", flush=True)
        if not ok:
            problems.append((title, module, failed_lines, proc))

    print()
    if problems:
        for title, module, failed_lines, proc in problems:
            print(f"--- {title} ({module}) ---")
            for line in failed_lines:
                print("  " + line)
            for line in proc.stdout.splitlines()[-5:]:
                print("  out: " + line)
            for line in (proc.stderr or "").strip().splitlines()[-8:]:
                print("  err: " + line)
        print(f"\n{len(problems)} script(s) FAILED")
        return 1

    print(f"Total: {total_passed} checks passed, 0 failed.")
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
