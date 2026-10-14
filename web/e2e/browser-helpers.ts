// Helpers of the browser journeys (browser B2; B3 to B12 import them). The browser service sits
// behind the compose profile `browser` and publishes no port: a journey reaches browserd the way
// the api does, from inside the stack, and reaches the lease's Chromium only through
// browserd.probe inside the browser container (CDP never leaves it). Every call names the
// journey's own compose project (E2E_COMPOSE_PROJECT), never the app's.
import { execFileSync } from "node:child_process";

import { root } from "./stack";

export type Lease = {
  lease_id: string;
  user_id: string;
  profile_id: string;
  purpose: string;
  state: "active" | "waiting_person";
  active_s: number;
  max_s: number;
};

export type Current = {
  lease: Lease | null;
  last_end: { lease_id: string; reason: string } | null;
};

function project(): string {
  const name = process.env.E2E_COMPOSE_PROJECT;
  if (!name)
    throw new Error("E2E_COMPOSE_PROJECT names the journey's own stack");
  return name;
}

function compose(...args: string[]): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project(),
      "-f",
      "compose.yml",
      "--profile",
      "browser",
      ...args,
    ],
    { cwd: root, encoding: "utf8", timeout: 120_000 },
  );
}

/** One JSON line of browserd.probe (snapshot, navigate, sandbox, env, front) on the lease. */
export function probe(
  action: string,
  ...args: string[]
): Record<string, unknown> {
  const out = compose(
    "exec",
    "-T",
    "browser",
    "python",
    "-m",
    "browserd.probe",
    action,
    ...args,
  );
  return JSON.parse(out) as Record<string, unknown>;
}

/** The current lease as browserd sees it, asked from the api's container with the stack's own
 * secret (read inside that container, never printed). */
export function currentLease(): Current {
  const code = [
    "import os, urllib.request",
    "req = urllib.request.Request('http://browser:9300/leases/current')",
    "req.add_header('Authorization', 'Bearer ' + os.environ['BROWSER_CONTROL_SECRET'])",
    "print(urllib.request.urlopen(req, timeout=10).read().decode())",
  ].join("\n");
  return JSON.parse(
    compose("exec", "-T", "api", "python", "-c", code),
  ) as Current;
}

/** The uid that owns a profile directory and its mode, read inside the browser container. */
export function profileOwner(profileId: string): { mode: string; uid: number } {
  if (!/^[0-9a-f-]{36}$/.test(profileId)) throw new Error("not a profile id");
  const [mode, uid] = compose(
    "exec",
    "-T",
    "browser",
    "stat",
    "-c",
    "%a %u",
    `/profiles/${profileId}`,
  )
    .trim()
    .split(" ");
  return { mode: mode ?? "", uid: Number(uid) };
}
