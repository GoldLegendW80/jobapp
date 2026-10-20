// Helpers of the browser B12b journey: an assistant's run of a person, in the worker's container
// of the journey's own stack (E2E_COMPOSE_PROJECT), that holds an `apply_fill` lease on the static
// pages, opens B12a's fixture site behind its login wall, and, when told, calls the agent tool
// `browser_request_signin` exactly as an agent's run does (app/browser/signin_tool.py). The run
// prints one JSON line per step; the journey reads them and ends the process in every case.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";

import { root } from "./stack";

const RUN = `
import asyncio, json, sys, uuid
from sqlalchemy import select
from app.agents import tools as app_tools
from app.browser import signin_tool
from app.browser.client import BrowserdClient
from app.browser.leases import DbProfiles, Leases, lease_max_table
from app.browser.tools import LeaseTools
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.identity.models import User
from app.queue import Queue

def say(**line):
    print(json.dumps(line), flush=True)

async def main(email, address):
    s = get_settings()
    sessions = build_sessionmaker(build_engine(s))
    with sessions() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
    secret = s.browser_control_secret
    client = BrowserdClient(s.browser_url, secret.get_secret_value() if secret else None)
    store = Leases(
        Queue(s.redis_url.get_secret_value()).pool, client, DbProfiles(sessions),
        idle_s=s.browser_idle_s, session_max_s=s.browser_session_max_s,
        max_table=lease_max_table(s.browser_lease_max_s),
    )
    got = await store.acquire(str(user_id), "apply_fill", True, hosts=["pages"])
    if got.lease is None or got.token is None:
        say(step="busy")
        return
    tools = LeaseTools(store, client, str(user_id), got.lease.lease_id, got.token)
    try:
        await tools.navigate(address)
        page = str((await tools.snapshot()).get("snapshot") or "")
        say(step="ready", signed_in="Signed in as" in page)
        if not (await asyncio.to_thread(sys.stdin.readline)).strip():
            say(step="stopped")
            return
        run_id = uuid.uuid4()
        signin_tool.hold(lambda scope: signin_tool.Hold(tools) if scope.run_id == run_id else None)
        scope = app_tools.Scope(sessions=sessions, user_id=user_id, run_id=run_id)
        made = app_tools.FACTORIES[signin_tool.TOOL](scope)
        answer = await made.handler({"site": "Example Jobs", "reason": "The application asks to sign in first."})
        text = answer["content"][0]["text"]
        page = ""
        if not answer.get("is_error"):
            page = str((await tools.snapshot()).get("snapshot") or "")
        say(step="answer", text=text, error=bool(answer.get("is_error")), signed_in="Signed in as" in page)
        current = (await client.current()).get("lease") or {}
        say(step="lease", state=current.get("state"), same=current.get("lease_id") == got.lease.lease_id)
    finally:
        await store.release(str(user_id), got.lease.lease_id, "apply_fill_done")

asyncio.run(main(sys.argv[1], sys.argv[2]))
`;

export type Line = Record<string, unknown> & { step: string };

/** One assistant's run of `email`: `next()` reads its next line, `ask()` calls the tool. */
export class AgentRun {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly lines: AsyncIterator<string>;

  constructor(email: string, address: string) {
    const project = process.env.E2E_COMPOSE_PROJECT;
    if (!project)
      throw new Error("E2E_COMPOSE_PROJECT names the journey's own stack");
    this.child = spawn(
      "docker",
      [
        "compose",
        "-p",
        project,
        "-f",
        "compose.yml",
        "--profile",
        "browser",
        "exec",
        "-T",
        "worker",
        "python",
        "-c",
        RUN,
        email,
        address,
      ],
      { cwd: root },
    );
    this.lines = createInterface({ input: this.child.stdout })[
      Symbol.asyncIterator
    ]();
  }

  async next(): Promise<Line> {
    for (;;) {
      const read = await this.lines.next();
      if (read.done) throw new Error("the assistant's run ended early");
      const text = read.value.trim();
      if (text.startsWith("{")) return JSON.parse(text) as Line;
    }
  }

  ask(): void {
    this.child.stdin.write("go\n");
  }

  /** Waits for the run's end, or ends it: no process outlives the test. */
  async close(): Promise<void> {
    if (this.child.exitCode !== null) return;
    const ended = new Promise((done) => this.child.once("exit", done));
    this.child.stdin.end();
    const timer = setTimeout(() => this.child.kill(), 60_000);
    await ended;
    clearTimeout(timer);
  }
}
