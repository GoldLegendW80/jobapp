"""Peak memory of the api process under a flood of sign ins (issue #108).

Starts the real api (uvicorn, one process, production argon2 parameters) from a checkout, on a
throwaway Postgres and Redis, and records the resident memory of that process (`ps -o rss`,
sampled every 25 ms) at rest, under sign ins sent all at once from callers with no session, and
under a burst of ten real sign ins. Synthetic accounts only (e2e-p108-*@example.org).

    uv run --native-tls --directory api python ../tasks/evidence/identity-108/measure.py \
        --api-dir <checkout>/api --database-url postgresql+psycopg://... \
        --redis-url redis://127.0.0.1:PORT/0 --out ../tasks/evidence/identity-108/after.json

The database must be at the head migration. Run it once per checkout (before: origin/main).
"""

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import sqlalchemy as sa

PASSWORD = "correct horse battery 42"
HEADERS = {"X-Requested-With": "jobapp"}
USERS = [f"e2e-p108-{i}@example.org" for i in range(10)]


def seed(database_url: str) -> None:
    sys.path.insert(0, str(Path.cwd()))  # run from the api folder, see the usage above
    from app.identity import security
    from app.identity.models import Role, User

    engine = sa.create_engine(database_url)
    password_hash = security.hash_password(PASSWORD)
    with engine.begin() as db:
        db.execute(
            sa.text("TRUNCATE login_attempts, sessions RESTART IDENTITY CASCADE")
        )
        db.execute(sa.delete(User).where(User.email.like("e2e-p108-%")))
        for email in USERS:
            db.execute(
                sa.insert(User).values(
                    email=email,
                    name="E2E",
                    role=Role.member,
                    password_hash=password_hash,
                    is_active=True,
                )
            )
    engine.dispose()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Sampler:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.peak_kib = 0
        self.last_kib = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            out = subprocess.run(
                ["/bin/ps", "-o", "rss=", "-p", str(self.pid)],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if out:
                self.last_kib = int(out)
                self.peak_kib = max(self.peak_kib, self.last_kib)
            time.sleep(0.025)

    def reset(self) -> None:
        self.peak_kib = self.last_kib

    def peak_mib(self) -> int:
        return round(self.peak_kib / 1024)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def at_once(
    base: str, bodies: list[tuple[str, str]], first_ip: int
) -> dict[str, object]:
    barrier = threading.Barrier(len(bodies))

    def send(numbered: tuple[int, tuple[str, str]]) -> tuple[int, str | None, float]:
        i, (email, password) = numbered
        n = first_ip + i
        headers = {
            **HEADERS,
            "X-Forwarded-For": f"198.{18 + n // 62500}.{n // 250 % 250}.{1 + n % 250}",
        }
        barrier.wait(timeout=60)
        started = time.monotonic()
        try:
            response = httpx.post(
                f"{base}/api/auth/login",
                json={"email": email, "password": password},
                headers=headers,
                timeout=120,
            )
        except httpx.HTTPError as exc:
            return 0, type(exc).__name__, time.monotonic() - started
        return (
            response.status_code,
            response.headers.get("Retry-After"),
            time.monotonic() - started,
        )

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
        results = list(pool.map(send, enumerate(bodies)))
    return {
        "requests": len(bodies),
        "statuses": dict(sorted(Counter(str(code) for code, _, _ in results).items())),
        "retry_after_of_503": sorted(
            {str(after) for code, after, _ in results if code == 503}
        ),
        "all_done_in_s": round(time.monotonic() - started, 2),
        "slowest_answer_s": round(max(took for _, _, took in results), 2),
        "fastest_answer_s": round(min(took for _, _, took in results), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-dir", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    seed(args.database_url)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATABASE_URL": args.database_url,
        "REDIS_URL": args.redis_url,
        "APP_ENV": "development",
        "LOG_LEVEL": "error",
    }
    command = [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory"]
    command += ["--host", "127.0.0.1", "--port", str(port), "--log-level", "error"]
    command += ["--proxy-headers", "--forwarded-allow-ips", "*"]
    server = subprocess.Popen(command, cwd=args.api_dir, env=env)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{base}/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise SystemExit("the api did not start")
            time.sleep(0.2)
        sampler = Sampler(server.pid)
        time.sleep(1.5)
        report: dict[str, object] = {
            "label": args.label,
            "api_dir": str(Path(args.api_dir).resolve()),
            "machine": "development Mac, not the Jetson; resident memory of the uvicorn process",
            "rest_mib": sampler.peak_mib(),
        }

        times = []
        for i in range(6):
            started = time.monotonic()
            httpx.post(
                f"{base}/api/auth/login",
                json={
                    "email": f"e2e-p108-nobody-seq-{i}@example.org",
                    "password": PASSWORD,
                },
                headers={**HEADERS, "X-Forwarded-For": f"198.17.0.{1 + i}"},
                timeout=60,
            )
            times.append(time.monotonic() - started)
        report["one_sign_in_at_a_time"] = {
            "median_answer_ms": round(statistics.median(times[1:]) * 1000),
            "peak_mib": sampler.peak_mib(),
        }

        first_ip = 0
        for name, count in (("flood_40", 40), ("flood_120", 120)):
            time.sleep(2)
            sampler.reset()
            bodies = [
                (f"e2e-p108-nobody-{name}-{i}@example.org", PASSWORD)
                for i in range(count)
            ]
            result = at_once(base, bodies, first_ip)
            first_ip += count
            report[name] = {**result, "peak_mib": sampler.peak_mib()}

        time.sleep(2)
        sampler.reset()
        result = at_once(base, [(email, PASSWORD) for email in USERS], first_ip)
        report["burst_of_10_real_sign_ins"] = {**result, "peak_mib": sampler.peak_mib()}
        time.sleep(2)
        report["rest_afterwards_mib"] = round(sampler.last_kib / 1024)
        sampler.stop()
    finally:
        server.terminate()
        server.wait(timeout=20)

    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
