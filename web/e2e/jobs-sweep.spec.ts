import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";

import {
  caddyUrl,
  cliLink,
  makeShots,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Jobs J5 against the real stack through Caddy: "Find jobs" with live progress, the summary
 * (new postings as links, failed boards), the sweep history, the list refreshed; a 5,000 posting
 * sweep with the worker's memory sampled; and one REAL sweep of two real boards (Mistral AI on
 * Ashby, Anthropic on Greenhouse) for an e2e account.
 *
 * Local scratch stack (tests/compose.j5-e2e.yml adds the fake ATS server the worker reaches for
 * "fake-" orgs):
 *   HTTP_PORT=8125 docker compose -p jobapp-j5e2e -f compose.yml -f tests/compose.j5-e2e.yml \
 *     up -d --build --wait
 *   docker compose -p jobapp-j5e2e -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8125 E2E_COMPOSE_PROJECT=jobapp-j5e2e \
 *     pnpm --dir web exec playwright test jobs-sweep.spec.ts
 * CI runs it on the scratch stack of tests/ats-stub.compose.yml (the worker reads the same fake
 * ATS for every org, a real org answering 404) with E2E_SKIP_REAL_SWEEP=1: every journey except
 * the real sweep.
 * E2E_REAL_ONLY=1 (the Jetson, tests/e2e_jobs_sweep_jetson.sh): only the real sweep, for an e2e
 * account; nothing of Harald's is read or touched. The stats of the real sweep are written to
 * tasks/evidence/<evidence>/real-sweep.json.
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const realOnly = Boolean(process.env.E2E_REAL_ONLY);
const evidenceName = process.env.E2E_EVIDENCE ?? "jobs-j5";
const shots = makeShots("jobs-j5");
const run = Date.now().toString(36);
const EMAIL =
  process.env.E2E_ADMIN_EMAIL ?? `e2e-jobs-sweep-${run}@example.org`;
const NAME = "Sweep Tester";
const PASSWORD = `sweep tester ${run} passphrase`;
const CSRF = { "X-Requested-With": "jobapp" };

const SKILLS = {
  years_experience: 3.5,
  have: [
    { name: "agents", terms: ["agent", "agentic", "tool use"] },
    { name: "python", terms: ["python", "fastapi"] },
    { name: "llm", terms: ["llm", "evaluation"] },
  ],
  adjacent: [{ name: "rag", terms: ["rag", "retrieval"] }],
  none: [{ name: "research", terms: ["pytorch", "publication"] }],
  gates: [],
};

const terms = (list: string[], advanced = false) => ({
  terms: list,
  advanced_regex: advanced,
});

const board = (company: string, ats: string, org: string) => ({
  company,
  ats,
  org,
  enabled: true,
  extra_exclude: terms([]),
});

function search(boards: ReturnType<typeof board>[], wide = false) {
  return {
    title_include: wide
      ? terms(
          [
            "engineer",
            "applied ai",
            "forward deployed",
            "solutions? (architect|engineer)",
            "deployment",
            "agent",
          ],
          true,
        )
      : terms(["ai engineer", "forward deployed", "genai", "مهندس"]),
    title_exclude: terms(["account executive", "sales"]),
    location: wide
      ? terms(["paris", "france", "london", "europe", "emea", "remote"])
      : terms(["paris", "london", "europe", "remote"]),
    contract_types: [],
    remote_stance: "any",
    min_fit: 70,
    days: 365,
    boards,
    never_apply: ["No security roles: security, cyber (e2e rule)"],
  };
}

async function saveSection(
  request: APIRequestContext,
  section: string,
  data: unknown,
) {
  const profile = await request.get("/api/profile");
  expect(profile.ok()).toBe(true);
  const { revision } = (await profile.json()) as { revision: number };
  const saved = await request.put(`/api/profile/${section}`, {
    headers: CSRF,
    data: { base_revision: revision, data },
  });
  expect(saved.ok(), await saved.text()).toBe(true);
}

interface SweepJson {
  id: string;
  status: string;
  boards_total: number;
  boards_ok: number;
  boards_failed: Array<{ company: string; error: string }>;
  seen: number;
  matched: number;
  new: number;
  duplicates: number;
  skipped_by_rule: number;
  duration: number | null;
  error: string | null;
}

async function latestSweep(
  request: APIRequestContext,
): Promise<SweepJson | undefined> {
  const response = await request.get("/api/jobs/sweeps?limit=1");
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { items: SweepJson[] };
  return body.items[0];
}

async function lastSweep(request: APIRequestContext): Promise<SweepJson> {
  const found = await latestSweep(request);
  if (!found) throw new Error("no sweep yet");
  return found;
}

function compose(...args: string[]): string[] {
  return [
    "compose",
    "-p",
    process.env.E2E_COMPOSE_PROJECT ?? "jobapp",
    "-f",
    "compose.yml",
    ...args,
  ];
}

/** The worker container's memory in MiB, from docker stats (local stack only). */
function workerMemoryMib(): number | undefined {
  if (!process.env.E2E_COMPOSE_PROJECT) return undefined;
  const id = execFileSync("docker", compose("ps", "-q", "worker"), {
    cwd: root,
    encoding: "utf8",
  }).trim();
  const usage = execFileSync(
    "docker",
    ["stats", "--no-stream", "--format", "{{.MemUsage}}", id],
    { encoding: "utf8" },
  ).trim();
  const match = /^([\d.]+)(KiB|MiB|GiB)/.exec(usage);
  if (!match) return undefined;
  const value = Number(match[1]);
  return match[2] === "GiB"
    ? value * 1024
    : match[2] === "KiB"
      ? value / 1024
      : value;
}

function writeEvidence(name: string, data: unknown) {
  const dir = path.join(root, "tasks/evidence", evidenceName);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name), `${JSON.stringify(data, null, 2)}\n`);
}

/** Nothing in the dialog is wider than the dialog, at desktop and at 375px. */
async function fitsInside(page: Page, dialog: ReturnType<Page["getByRole"]>) {
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 800 });
    const overflow = await dialog.evaluate((el) => {
      const box = el.getBoundingClientRect();
      let widest = 0;
      for (const child of Array.from(el.querySelectorAll("*")))
        widest = Math.max(
          widest,
          child.getBoundingClientRect().right - box.right,
        );
      return Math.max(widest, el.scrollWidth - el.clientWidth);
    });
    expect(overflow, `dialog at ${width}px`).toBeLessThanOrEqual(1);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
}

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, [[409, /\/api\/jobs\/sweeps/]]);
});

test.afterAll(async () => {
  await context.close();
});

test("an account with skills", async () => {
  test.setTimeout(120_000);
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await saveSection(page.request, "skills", SKILLS);
});

test("Find jobs: live progress, summary, history, list refreshed", async () => {
  test.skip(realOnly, "fake boards only on the local stack");
  test.setTimeout(240_000);
  await saveSection(
    page.request,
    "search",
    search([
      board("Acme AI", "ashby", "fake-ok"),
      board("Société Ünïcode", "greenhouse", "fake-unicode"),
      board("Guard Co", "lever", "fake-security"),
      board("Gone Corp", "lever", "fake-404"),
      board("Slow Corp", "ashby", "fake-timeout"),
    ]),
  );
  await page.goto("/jobs");
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    "0 postings",
  );
  // Progress must come over the event stream through Caddy, not the polling fallback.
  const polls: string[] = [];
  const onRequest = (request: { url: () => string }) => {
    if (
      /\/api\/jobs\/sweeps\/[0-9a-f-]{36}$/.test(
        new URL(request.url()).pathname,
      )
    )
      polls.push(request.url());
  };
  page.on("request", onRequest);
  const stream = page.waitForResponse(
    /\/api\/jobs\/sweeps\/[0-9a-f-]{36}\/events$/,
  );
  await page.getByRole("button", { name: "Find jobs" }).click();
  expect((await stream).headers()["content-type"]).toContain(
    "text/event-stream",
  );

  const progress = page.getByRole("region", { name: "Sweep in progress" });
  await expect(progress).toBeVisible();
  await expect(progress).toContainText(/4 of 5 boards read/, {
    timeout: 30_000,
  });
  await expect(progress).toContainText("Reading Slow Corp");
  await expect(
    page.getByRole("button", { name: "Finding jobs" }),
  ).toBeDisabled();
  await shots(page, "find-jobs-progress", false);

  // Slow Corp times out three times (20 s each) before the sweep ends.
  const dialog = page.getByRole("dialog", { name: "5 new postings found" });
  await expect(dialog).toBeVisible({ timeout: 120_000 });
  page.off("request", onRequest);
  expect(polls).toEqual([]);
  await expect(dialog).toContainText("3 boards read");
  const failed = dialog.getByRole("region", { name: "Failed boards" });
  await expect(failed).toContainText("2 boards could not be read");
  await expect(failed).toContainText(
    "Gone Corp (lever, fake-404): board not found",
  );
  await expect(failed).toContainText(
    "Slow Corp (ashby, fake-timeout): no answer in time",
  );
  await expect(dialog).toContainText(
    "1 new posting matched a never apply rule and was set to Skipped.",
  );
  const links = dialog
    .getByRole("region", { name: "New postings" })
    .getByRole("link");
  await expect(links).toHaveCount(5);
  await fitsInside(page, dialog);
  await shots(page, "find-jobs-summary", false);

  // The list refreshed behind the dialog.
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    "5 postings",
  );
  await expect(page.getByRole("table", { name: "Postings" })).toContainText(
    "AI Engineer 東京 🤖",
  );
  await shots(page, "jobs-after-sweep");

  // A new posting opens from the summary of the history.
  await page.getByRole("button", { name: "Sweep history" }).click();
  const history = page.getByRole("dialog", { name: "Sweeps" });
  // Automatic search is off for every account until its owner turns it on.
  await expect(
    history.getByRole("region", { name: "Automatic search" }),
  ).toContainText("Automatic search: off.");
  await expect(
    history.getByRole("list", { name: "Last sweeps" }),
  ).toContainText(/By hand, Done, 2 boards failed/);
  await fitsInside(page, history);
  await shots(page, "sweep-history", false);
  await history
    .getByRole("list", { name: "Last sweeps" })
    .getByRole("button")
    .first()
    .click();
  const again = page.getByRole("dialog", { name: "5 new postings found" });
  await again
    .getByRole("link", { name: /Applied AI Engineer, Agents/ })
    .click();
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Applied AI Engineer, Agents",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("list", { name: "History, newest first" }),
  ).toBeVisible();

  // A second sweep at once is refused; the running one is followed instead.
  const first = await page.request.post("/api/jobs/sweeps", { headers: CSRF });
  expect(first.status()).toBe(202);
  const second = await page.request.post("/api/jobs/sweeps", { headers: CSRF });
  expect(second.status()).toBe(409);
  expect(((await second.json()) as { sweep_id: string }).sweep_id).toBe(
    ((await first.json()) as { id: string }).id,
  );
  await expect
    .poll(async () => (await lastSweep(page.request)).status, {
      timeout: 120_000,
    })
    .toBe("done");
  const rerun = await lastSweep(page.request);
  expect(rerun.new).toBe(0);
  expect(rerun.duplicates).toBe(5);
  expect(errors).toEqual([]);
});

/** "HH:MM" in Paris, `minutes` from now. */
function parisClock(minutes: number): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Paris",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(Date.now() + minutes * 60_000));
}

test("Schedule: off by default, turned on, fires at its time, turned off", async () => {
  test.skip(realOnly, "fake boards only on the local stack");
  test.setTimeout(600_000);
  await saveSection(
    page.request,
    "search",
    search([board("Acme Two", "lever", "fake-ok2")]),
  );
  await page.goto("/jobs");
  const button = page.getByRole("button", { name: /^Schedule/ });
  await expect(button).toContainText("Off");
  await button.click();
  const dialog = page.getByRole("dialog", { name: "Automatic search" });
  await expect(dialog.getByRole("status")).toHaveText(
    "Automatic search is off.",
  );
  await dialog.getByRole("checkbox", { name: "Search automatically" }).check();
  // The next 5 minute tick of the worker after this time fires it.
  const time = parisClock(1);
  await dialog.getByLabel("Time 1", { exact: true }).fill(time);
  await dialog.getByRole("button", { name: "Add a time" }).click();
  await dialog.getByLabel("Time 2", { exact: true }).fill("18:30");
  await expect(dialog.getByRole("status")).toContainText(
    `Next automatic search: `,
  );
  await fitsInside(page, dialog);
  await shots(page, "schedule-dialog", false);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(dialog).toBeHidden();
  await expect(button).toContainText("On");

  const profile = (await (await page.request.get("/api/profile")).json()) as {
    sections: { search: { sweep_schedule: unknown } };
  };
  expect(profile.sections.search.sweep_schedule).toEqual({
    enabled: true,
    days: [
      "monday",
      "tuesday",
      "wednesday",
      "thursday",
      "friday",
      "saturday",
      "sunday",
    ],
    times: [time, "18:30"].sort(),
    timezone: "Europe/Paris",
  });
  const listed = (await (
    await page.request.get("/api/jobs/sweeps")
  ).json()) as {
    schedule: { active: boolean; next_run: string };
  };
  expect(listed.schedule.active).toBe(true);
  await page.getByRole("button", { name: "Sweep history" }).click();
  const history = page.getByRole("dialog", { name: "Sweeps" });
  await expect(
    history.getByRole("region", { name: "Automatic search" }),
  ).toContainText(". Next: ");
  await shots(page, "sweep-history-scheduled", false);
  await history.getByRole("button", { name: "Close" }).click();

  // The worker's tick starts it by itself, once, as a schedule sweep.
  await expect
    .poll(
      async () => {
        const body = (await (
          await page.request.get("/api/jobs/sweeps")
        ).json()) as {
          items: Array<{ trigger: string; status: string; new: number }>;
        };
        const automatic = body.items.filter((i) => i.trigger === "schedule");
        return automatic.length
          ? `${automatic.length} ${automatic[0]!.status}`
          : "none";
      },
      { timeout: 420_000, intervals: [5_000] },
    )
    .toBe("1 done");
  writeEvidence("scheduled-sweep.json", {
    at: new Date().toISOString(),
    scheduled_time_paris: time,
    sweep: await lastSweep(page.request),
  });

  // Off again.
  await button.click();
  await dialog
    .getByRole("checkbox", { name: "Search automatically" })
    .uncheck();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(button).toContainText("Off");
});

test("5,000 postings: the worker stays far under its 1536 MiB", async () => {
  test.skip(realOnly, "fake boards only on the local stack");
  test.setTimeout(300_000);
  await saveSection(
    page.request,
    "search",
    search([
      board("Big Ashby", "ashby", "fake-big"),
      board("Big Greenhouse", "greenhouse", "fake-big"),
      board("Big Lever", "lever", "fake-big"),
    ]),
  );
  const started = await page.request.post("/api/jobs/sweeps", {
    headers: CSRF,
  });
  expect(started.status()).toBe(202);
  const samples: number[] = [];
  const began = Date.now();
  for (;;) {
    const memory = workerMemoryMib();
    if (memory !== undefined) samples.push(memory);
    const sweep = await lastSweep(page.request);
    if (sweep.status !== "running") {
      expect(sweep.status).toBe("done");
      expect(sweep.seen).toBe(15_000);
      expect(sweep.new).toBe(5_000);
      expect(sweep.duplicates).toBe(10_000);
      const peak = samples.length ? Math.max(...samples) : null;
      writeEvidence("memory-5000.json", {
        seen: sweep.seen,
        new: sweep.new,
        duration_s: sweep.duration,
        wall_s: (Date.now() - began) / 1000,
        worker_memory_mib_samples: samples.length,
        worker_memory_mib_peak: peak,
        worker_memory_limit_mib: 1536,
      });
      if (peak !== null) expect(peak).toBeLessThan(1536);
      break;
    }
    expect(Date.now() - began).toBeLessThan(280_000);
    await page.waitForTimeout(500);
  }
});

test("a real sweep of two real boards", async () => {
  // CI (.github/workflows/ci.yml): the fake boards only, never a real ATS.
  test.skip(
    Boolean(process.env.E2E_SKIP_REAL_SWEEP),
    "E2E_SKIP_REAL_SWEEP: no real ATS from CI",
  );
  test.setTimeout(300_000);
  await saveSection(
    page.request,
    "search",
    search(
      [
        board("Mistral AI", "ashby", "mistral.ai"),
        board("Anthropic", "greenhouse", "anthropic"),
      ],
      true,
    ),
  );
  await page.goto("/jobs");
  const before = await latestSweep(page.request);
  await page.getByRole("button", { name: "Find jobs" }).click();
  await expect(
    page.getByRole("region", { name: "Sweep in progress" }),
  ).toBeVisible();
  const dialog = page.getByRole("dialog", { name: /new posting/ });
  await expect(dialog).toBeVisible({ timeout: 240_000 });
  const sweep = await lastSweep(page.request);
  expect(sweep.id).not.toBe(before?.id);
  writeEvidence("real-sweep.json", {
    at: new Date().toISOString(),
    stack: caddyUrl,
    boards: [
      "Mistral AI (ashby, mistral.ai)",
      "Anthropic (greenhouse, anthropic)",
    ],
    status: sweep.status,
    boards_ok: sweep.boards_ok,
    boards_failed: sweep.boards_failed,
    seen: sweep.seen,
    matched: sweep.matched,
    new: sweep.new,
    duplicates: sweep.duplicates,
    skipped_by_rule: sweep.skipped_by_rule,
    duration_s: sweep.duration,
    worker_memory_mib: workerMemoryMib() ?? null,
  });
  expect(sweep.status).toBe("done");
  expect(sweep.boards_failed).toEqual([]);
  expect(sweep.boards_ok).toBe(2);
  expect(sweep.seen).toBeGreaterThan(100);
  expect(sweep.matched).toBeGreaterThan(0);
  await fitsInside(page, dialog);
  await shots(page, "real-sweep-summary", false);
  await dialog.getByRole("button", { name: "Done" }).click();
  expect(errors).toEqual([]);
});
