import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addPosting,
  caddyUrl,
  CSRF,
  invented,
  readDocument,
  saveDraft,
  seedDocuments,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Documents D17 against the real stack through Caddy, the worker on the fake model backend and
 * the real renderer, one fresh account and only invented data: a posting's CV rendered once
 * through the api (as D12's journey ends), then, through the page's Chat view, "shorter summary"
 * answered with what changed and the new version, and "use the banner template" answered with
 * the switch, the content unchanged and the preview drawn with the new template. The chat runs
 * the recorded streams `documents_chat_cv_journey` and `documents_chat_cv_banner`, named in the
 * message's `script`, added here on the way out of the browser (D12's pattern for Write now).
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8099 docker compose -p jobapp-d17 -f compose.yml -f compose.mac.yml up -d --wait
 *   docker compose -p jobapp-d17 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d17 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d17.spec.ts
 *   docker compose -p jobapp-d17 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d17";
const SEPARATOR = /[–—]| - /;
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
// The recorded stream of each message the journey sends.
const SCRIPTS: Record<string, string> = {
  "shorter summary": "journey",
  "use the banner template": "banner",
};
const SHORTER =
  "Platform engineer with about 3.5 years of professional engineering, running the deployment platform of the billing team at Quillmere Example Works.";

// The CV D12's journey writes (api/app/agents/fake_streams/documents_cv_journey.json): the
// chat's stream `documents_chat_cv_journey` changes its summary only.
const WRITTEN: Record<string, unknown> = {
  schema: 1,
  target: "Brindle Example Labs, Senior Platform Engineer",
  headline: "Platform Engineer for teams that ship every day",
  summary:
    "Platform engineer with about 3.5 years of professional engineering. At Quillmere Example Works I run the deployment platform of the billing team.",
  highlights: [
    "Deployment platform of the billing team",
    "Backend services in Python",
    "Internal tools in Python and React",
  ],
  competencies: [
    "Kubernetes",
    "Terraform",
    "Python",
    "FastAPI",
    "PostgreSQL",
    "CI/CD",
    "Platform engineering",
    "React",
  ],
  experience: [
    {
      employer_key: "quillmere",
      bullets: [
        "Run the deployment platform of the billing team, from the first commit to production.",
        "Wrote the platform's backend services in Python with FastAPI.",
      ],
    },
    {
      employer_key: "tarnwick",
      bullets: [
        "Built internal tools in Python and React for the support team.",
      ],
    },
  ],
  skills: [
    { label: "Cloud", items: ["Kubernetes", "Terraform"] },
    { label: "Backend", items: ["Python", "FastAPI", "PostgreSQL"] },
    { label: "Frontend", items: ["React", "TypeScript"] },
  ],
  notes: "",
};

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let postingId: string;
let cvId: string;

test.beforeAll(async ({ browser }) => {
  person = invented("d17");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
  await page.route("**/api/documents/*/messages", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") {
      await route.continue();
      return;
    }
    const body = request.postDataJSON() as { text: string };
    await route.continue({
      postData: JSON.stringify({ ...body, script: SCRIPTS[body.text] }),
    });
  });
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

async function ask(text: string) {
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill(text);
  await box.press("Enter");
  await expect(
    page.getByTestId("document-chat-message").filter({ hasText: text }),
  ).toBeVisible();
}

function lastAnswer() {
  return page
    .getByTestId("document-chat-message")
    .and(page.locator('[data-role="assistant"]'))
    .last();
}

test("a posting's CV is rendered once, with the consent and a credential", async () => {
  await signUp(page, person);
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  const asked = await page.request.get("/api/documents/consent");
  const { version } = (await asked.json()) as { version: number };
  const accepted = await page.request.post("/api/documents/consent", {
    headers: CSRF,
    data: { version },
  });
  expect(accepted.status()).toBe(200);
  postingId = await addPosting(page.request, person);
  cvId = seedDocuments(person, postingId).cv;
  await saveDraft(page.request, cvId, WRITTEN);
  const rendered = await waitForRender(page.request, cvId, 1);
  expect(rendered.latest?.number).toBe(1);
});

test("shorter summary is answered with what changed and the new version", async () => {
  await page.goto(`/jobs/${postingId}/documents/cv`);
  await page.getByRole("tab", { name: "Chat" }).click();
  await expect(page.getByTestId("document-chat-empty")).toBeVisible();
  await ask("shorter summary");
  await expect(lastAnswer()).toContainText("Changed the summary.", {
    timeout: 60_000,
  });
  await expect(lastAnswer()).not.toContainText("skills");
  await expect(lastAnswer()).toContainText("This is version 2.");
  await expect(page.getByTestId("document-chat-writing")).toHaveCount(0);
  const drawn = await waitForRender(page.request, cvId, 2);
  expect(drawn.draft.summary).toBe(SHORTER);
  const raw = (await (
    await page.request.get(`/api/documents/${cvId}`)
  ).json()) as { draft_written_by: string };
  expect(raw.draft_written_by).toBe("chat");
  expect(await page.getByTestId("document-chat").textContent()).not.toMatch(
    SEPARATOR,
  );
  await makeShots(EVIDENCE, "d17")(page, "chat");
});

test("use the banner template switches it, the text unchanged, and the preview follows", async () => {
  const before = await readDocument(page.request, cvId);
  await ask("use the banner template");
  await expect(lastAnswer()).toContainText("Switched the template to Banner.", {
    timeout: 60_000,
  });
  await expect(lastAnswer()).toContainText("This is version 3.");
  const drawn = await waitForRender(page.request, cvId, 3);
  expect(drawn.template_slug).toBe("banner");
  expect(drawn.latest?.template_slug).toBe("banner");
  expect(drawn.draft).toEqual(before.draft);
  await expect(page.getByTestId("document-view")).toContainText("Banner");
  await page.getByRole("tab", { name: "See" }).click();
  await expect(page.getByRole("region", { name: "Versions" })).toContainText(
    "banner",
    { ignoreCase: true },
  );
  await makeShots(EVIDENCE, "d17")(page, "template");
  expect(errors).toEqual([]);
});
