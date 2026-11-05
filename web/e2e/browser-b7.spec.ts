import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  appLogs,
  debuggingOf,
  endLease,
  profileOnDisk,
  restartBrowser,
  startAgentLease,
  windowTitles,
} from "./browser-b7-helpers";
import { currentLease, probe, profileOwner } from "./browser-helpers";
import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B7 against the real stack through Caddy, its browser and the static pages of
 * tests/browser_pages/ (tests/browser-pages.compose.yml), no mocks. Person A, on a phone sized
 * page: a switch sent as the address of a sign in session is refused by the api; then A adds the
 * fixture site (under the pages server's name with a dot, as a website of Find jobs needs) to
 * Find jobs and clicks its Sign in (B17): the session opens in the live view with A at the
 * keyboard and no debugging port in the container; A signs in on the site with the live view's
 * keyboard and clicks Done, and Account lists the site under "Websites you are signed in to"
 * (audit chapter 2: Account shows only those, sign in happens from Find jobs). An assistant's
 * lease of A then reads "Signed in as" with both cookies and the local storage, and so does one
 * after a restart of the browser container (person B's "Forget all logins" in between removes
 * B's own profile and leaves A's alone). A forgets all logins: the profile's directory goes, the
 * list is empty, and the next lease starts on a new directory, under a new uid, signed out.
 * Neither the typed address nor the name typed on the site is in a log of the stack.
 *
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 180_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-b7a-${run}@example.org`;
const B = `e2e-browser-b7b-${run}@example.org`;
const NAME = `e2e-alex-${run}`;
// The pages server under the alias J15 gives it (tests/browser-pages.compose.yml).
const HOST = "jobs.example-robotics.test";
const SITE = `http://${HOST}:8080/b7/site.html`;
const CSRF = { "X-Requested-With": "jobapp" };
const BAD_ADDRESS =
  "Type the address of the site, starting with https:// or http://.";
const FORGOTTEN =
  "Your logins are forgotten. Your browser starts empty next time.";
const KEPT = "session cookie: yes, lasting cookie: yes, storage: yes";
const NOTHING = "session cookie: no, lasting cookie: no, storage: no";
const LISTED = "Websites you are signed in to";
const PHONE = { width: 375, height: 812 };
const shots = makeShots("browser-b7");

type Browser = {
  available: boolean;
  profile: { exists: boolean; size_bytes: number; last_used_at: string | null };
};

let contexts: BrowserContext[] = [];
let pageA: Page;
let pageB: Page;
const errors: string[][] = [];
let profileA = "";
let uidA = 0;

async function browserOf(page: Page): Promise<Browser> {
  const answer = await page.request.get("/api/browser");
  expect(answer.status()).toBe(200);
  return (await answer.json()) as Browser;
}

/** What an assistant's lease of A reads on the fixture site, through CDP inside the container. */
function readSite(): { text: string; profile: string } {
  startAgentLease(A);
  try {
    const profile = currentLease().lease?.profile_id ?? "";
    const seen = probe("navigate", SITE, "20");
    expect(seen.ok).toBe(true);
    // The control: an assistant's lease has its debugging port.
    expect(debuggingOf(profileOwner(profile).uid).listening).not.toEqual([]);
    return { text: String(seen.text), profile };
  } finally {
    endLease(A);
  }
}

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [A, "Alex Example"],
    [B, "Blake Example"],
  ] as const) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    errors.push(watchErrors(page, [[422, /\/api\/browser\/sessions$/]]));
    await page.goto(cliLink(email));
    await setPassword(page, `browser b7 journey ${run} passphrase`, name);
    if (email === A) pageA = page;
    else pageB = page;
  }
});

test.afterAll(async () => {
  endLease(A);
  endLease(B);
  for (const page of [pageA, pageB]) {
    await page.request.delete("/api/browser/sessions/current", {
      headers: CSRF,
    });
  }
  for (const context of contexts) await context.close();
  contexts = [];
  clearAttempts([A, B]);
});

test("A signs in to the site through the live view, on a phone", async () => {
  await pageA.setViewportSize(PHONE);
  // A switch sent as the address is refused in the api's words, and nothing starts.
  const refused = await pageA.request.post("/api/browser/sessions", {
    headers: CSRF,
    data: { purpose: "signin", address: "--remote-debugging-port=9222" },
  });
  expect(refused.status()).toBe(422);
  expect(((await refused.json()) as { detail: string }).detail).toBe(
    BAD_ADDRESS,
  );
  expect(currentLease().lease).toBeNull();

  // Sign in happens from a website's line in Find jobs (B17); nothing is saved to the list.
  await pageA.goto("/jobs");
  await pageA.getByRole("button", { name: "Find jobs" }).click();
  const setup = pageA.getByRole("dialog", { name: "Find jobs" });
  await setup.getByLabel("Website address").fill(SITE);
  await setup.getByRole("button", { name: "Add", exact: true }).click();
  const row = setup.getByRole("listitem").filter({ hasText: HOST });
  await row.getByRole("button", { name: `Sign in to ${HOST}` }).click();
  await expect(
    setup.getByText(
      "This is your own session: you have the keyboard and the mouse.",
    ),
  ).toBeVisible({ timeout: 45_000 });
  await expect(
    setup.getByRole("button", { name: "Use the browser myself" }),
  ).toHaveCount(0);
  const held = currentLease().lease;
  expect(held?.purpose).toBe("signin");
  profileA = held?.profile_id ?? "";
  uidA = profileOwner(profileA).uid;

  // No debugging port in a person's own session, read inside the container.
  const seen = debuggingOf(uidA);
  expect(seen.processes).toBeGreaterThan(0);
  expect(seen.listening).toEqual([]);
  expect(seen.switches).toEqual([]);
  // browserd wrote no CDP port for this lease: the probe fails (its traceback is expected).
  expect(() => probe("navigate", SITE, "5")).toThrow();
  await expect
    .poll(windowTitles, { timeout: 30_000 })
    .toContain("Example Portal: signed out");

  // The phone's keyboard of the live view: Tab to the name field, the name, Enter.
  await setup.getByRole("button", { name: "Keyboard" }).click();
  const keys = setup.getByLabel("Type into your browser");
  await keys.press("Tab");
  await keys.pressSequentially(NAME);
  await keys.press("Enter");
  await expect
    .poll(windowTitles, { timeout: 30_000 })
    .toContain(`Example Portal: signed in as ${NAME}`);
  await shots(pageA, "signed-in");

  // Done ends the session and records the login the browser kept (the lasting cookie).
  await row.getByRole("button", { name: "Done" }).click();
  await expect(row).toContainText("Signed in", { timeout: 45_000 });
  await expect.poll(() => currentLease().lease).toBeNull();
  await setup.getByRole("button", { name: "Cancel" }).click();

  // Account lists the site, with its Sign in, and nothing else of the old card.
  await pageA.goto("/account");
  const listed = pageA.getByRole("list", { name: LISTED });
  await expect(listed.getByRole("listitem")).toHaveCount(1);
  await expect(listed.getByRole("listitem")).toContainText(HOST);
  await expect(
    listed.getByRole("button", { name: `Sign in to ${HOST}` }),
  ).toBeVisible();
  await expect(
    pageA.getByRole("button", { name: "Look at my browser" }),
  ).toHaveCount(0);
  await shots(pageA, "account-listed");
  // The end measured the profile and the api wrote its size.
  const after = await browserOf(pageA);
  expect(after.profile.size_bytes).toBeGreaterThan(0);
  expect(after.profile.last_used_at).not.toBeNull();
});

test("the login is in an assistant's lease, and after a restart of the browser", async () => {
  const first = readSite();
  expect(first.profile).toBe(profileA);
  expect(first.text).toContain(`Signed in as ${NAME}`);
  expect(first.text).toContain(KEPT);

  // B gets a profile of his own (an assistant's lease), then forgets his logins: his directory
  // goes, and A's profile is not B's to touch.
  startAgentLease(B);
  let profileB = "";
  try {
    profileB = currentLease().lease?.profile_id ?? "";
  } finally {
    endLease(B);
  }
  expect(profileB).not.toBe("");
  expect(profileB).not.toBe(profileA);
  expect(profileOnDisk(profileB)).toBe(true);
  await pageB.goto("/account");
  await pageB.getByRole("button", { name: "Forget all logins" }).click();
  await pageB
    .getByRole("alertdialog", { name: "Forget all logins?" })
    .getByRole("button", { name: "Forget all logins" })
    .click();
  await expect(pageB.getByText(FORGOTTEN)).toBeVisible();
  expect((await browserOf(pageB)).profile.exists).toBe(false);
  await expect
    .poll(() => profileOnDisk(profileB), { timeout: 90_000 })
    .toBe(false);
  expect(profileOnDisk(profileA)).toBe(true);

  restartBrowser();
  await expect
    .poll(async () => (await browserOf(pageA)).available, { timeout: 120_000 })
    .toBe(true);
  const again = readSite();
  expect(again.profile).toBe(profileA);
  expect(again.text).toContain(`Signed in as ${NAME}`);
  expect(again.text).toContain(KEPT);
});

test("A forgets all logins and the next lease starts empty", async () => {
  await pageA.goto("/account");
  await pageA.getByRole("button", { name: "Forget all logins" }).click();
  const dialog = pageA.getByRole("alertdialog", {
    name: "Forget all logins?",
  });
  await expect(dialog).toContainText("It holds about");
  await expect(dialog).toContainText(
    "Your accounts on those sites are not deleted.",
  );
  await shots(pageA, "forget");
  await dialog.getByRole("button", { name: "Forget all logins" }).click();
  await expect(pageA.getByText(FORGOTTEN)).toBeVisible();
  expect((await browserOf(pageA)).profile.exists).toBe(false);
  // The site's login went with the profile: the list says where sign in happens.
  await pageA.reload();
  await expect(
    pageA.getByText(
      "None yet. You sign in to a website from its line in Find jobs.",
    ),
  ).toBeVisible();
  // Removed at once when browserd can, else by the worker's drain every minute.
  await expect
    .poll(() => profileOnDisk(profileA), { timeout: 90_000 })
    .toBe(false);

  const fresh = readSite();
  expect(fresh.profile).not.toBe(profileA);
  expect(profileOwner(fresh.profile).uid).not.toBe(uidA);
  expect(fresh.text).toContain("Signed out");
  expect(fresh.text).toContain(NOTHING);
  for (const seen of errors) expect(seen).toEqual([]);
});

test("nothing A typed reaches a log of the stack", () => {
  const logs = appLogs();
  expect(logs.length).toBeGreaterThan(0);
  expect(logs).not.toContain(NAME);
  expect(logs).not.toContain("b7/site.html");
});
