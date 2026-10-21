// Helpers of the browser B7 journey, also used by B4's: an assistant's lease of a person started
// and ended through the api's own lease store (as the worker does, so `GET /api/browser` shows
// it), and what a session without a debugging port leaves to read from outside Chromium, inside
// the browser container: the window titles, the listening sockets and the switches of a uid.
// Every call names the journey's own compose project (E2E_COMPOSE_PROJECT), never the app's.
import { execFileSync } from "node:child_process";

import { root } from "./stack";

/** The static pages of tests/browser_pages/, as the stack's browser reaches them. */
export const PAGES = "http://pages:8080";

function compose(...args: string[]): string {
  const project = process.env.E2E_COMPOSE_PROJECT;
  if (!project)
    throw new Error("E2E_COMPOSE_PROJECT names the journey's own stack");
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project,
      "-f",
      "compose.yml",
      "--profile",
      "browser",
      ...args,
    ],
    // The stack's logs outgrow the default 1 MiB once every journey of the group ran (ENOBUFS).
    {
      cwd: root,
      encoding: "utf8",
      timeout: 120_000,
      maxBuffer: 64 * 1024 * 1024,
    },
  );
}

const AGENT_LEASE = `
import asyncio, sys
from sqlalchemy import select
from app.browser.client import BrowserdClient
from app.browser.leases import DbProfiles, Leases, lease_max_table
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.identity.models import User
from app.queue import Queue

async def main(action, email):
    s = get_settings()
    sessions = build_sessionmaker(build_engine(s))
    with sessions() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        raise SystemExit("no such person")
    secret = s.browser_control_secret
    store = Leases(
        Queue(s.redis_url.get_secret_value()).pool,
        BrowserdClient(s.browser_url, secret.get_secret_value() if secret else None),
        DbProfiles(sessions),
        idle_s=s.browser_idle_s,
        session_max_s=s.browser_session_max_s,
        max_table=lease_max_table(s.browser_lease_max_s),
    )
    if action == "start":
        got = await store.acquire(str(user_id), "apply_fill", True)
        if got.lease is None:
            raise SystemExit("the browser is not free")
        print(got.lease.lease_id)
    else:
        print(await store.release(str(user_id)))

asyncio.run(main(sys.argv[1], sys.argv[2]))
`;

/** Starts an assistant's lease (`apply_fill`, with CDP) of that person; answers its id. */
export function startAgentLease(email: string): string {
  return compose(
    "exec",
    "-T",
    "api",
    "python",
    "-c",
    AGENT_LEASE,
    "start",
    email,
  ).trim();
}

/** Ends whatever lease that person holds; true when there was one. */
export function endLease(email: string): boolean {
  return compose("exec", "-T", "api", "python", "-c", AGENT_LEASE, "end", email)
    .trim()
    .endsWith("True");
}

/** The titles of the windows on the browser's display, read as the display user. */
export function windowTitles(): string {
  return compose(
    "exec",
    "-T",
    "-u",
    "1000",
    "browser",
    "xwininfo",
    "-display",
    ":1",
    "-root",
    "-tree",
  );
}

const DEBUGGING = `
import json, os, sys
uid = sys.argv[1]
listening = []
for name in ("/proc/net/tcp", "/proc/net/tcp6"):
    for line in open(name).read().splitlines()[1:]:
        f = line.split()
        if f[3] == "0A" and f[7] == uid:
            listening.append(int(f[1].rsplit(":", 1)[1], 16))
processes, switches = 0, []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        if str(os.stat(f"/proc/{pid}").st_uid) != uid:
            continue
        argv = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace")
    except OSError:
        continue
    processes += 1
    switches += [a for a in argv.replace(" ", "\\0").split("\\0") if "remote-debugging" in a]
print(json.dumps({"listening": listening, "processes": processes, "switches": switches}))
`;

/** What the processes of a profile's uid listen on and were started with, inside the container. */
export function debuggingOf(uid: number): {
  listening: number[];
  processes: number;
  switches: string[];
} {
  return JSON.parse(
    compose("exec", "-T", "browser", "python", "-c", DEBUGGING, String(uid)),
  ) as { listening: number[]; processes: number; switches: string[] };
}

/** Whether a profile's directory is on the volume. */
export function profileOnDisk(profileId: string): boolean {
  if (!/^[0-9a-f-]{36}$/.test(profileId)) throw new Error("not a profile id");
  const out = compose(
    "exec",
    "-T",
    "browser",
    "python",
    "-c",
    "import os, sys; print(os.path.isdir('/profiles/' + sys.argv[1]))",
    profileId,
  );
  return out.trim() === "True";
}

/** The logs of the api, the worker, Caddy and the browser of the journey's stack. */
export function appLogs(): string {
  return compose("logs", "--no-color", "api", "worker", "caddy", "browser");
}

/** Restarts the browser container, its volume kept (`docker compose restart`, same config). */
export function restartBrowser(): void {
  compose("restart", "browser");
}
