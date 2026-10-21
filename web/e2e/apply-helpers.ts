import type {
  BrowserContext,
  FrameLocator,
  Locator,
  Page,
} from "@playwright/test";

/**
 * Helpers of the apply journeys (task A1). A later task never edits this file: it adds helpers
 * of its own in web/e2e/apply-a<n>-helpers.ts.
 *
 * Everything here talks to the fake ATS forms server of tests/ats-forms-stub.compose.yml
 * (service atsforms, tests/ats_forms_stub.py), never to a real job site. Its address on the
 * host is E2E_ATS_FORMS_URL (http://127.0.0.1:<ATS_FORMS_STUB_PORT>).
 *
 * - `stub`: `reset()` forgets what it recorded, `submissions()` reads every submission back with
 *   its fields and files, `mailbox()` the e-mail codes its code variant "sent", `file(sha)` a
 *   stored file, `posting(ats, variant)` the address of a posting of the catalogue.
 * - `unflagged`: makes a browser look unautomated (navigator.webdriver false), as the real
 *   Chrome of the browser module is; a plain Playwright browser fails the invisible check.
 * - `ashby`, `greenhouse`: the form mechanics, as a person would use them (button groups, the
 *   typeahead, react-select comboboxes, file inputs, boxes).
 */

export const atsFormsUrl = process.env.E2E_ATS_FORMS_URL;

export const PDF = Buffer.concat([
  Buffer.from("%PDF-1.7\n"),
  Buffer.from(Array.from({ length: 256 }, (_, i) => i)),
  Buffer.from("\n%%EOF\n"),
]);

export type Submission = {
  id: string;
  seq: number;
  ats: string;
  org: string;
  job: string;
  variant: string;
  step: string;
  fields: Record<string, string | string[]>;
  files: Array<{
    field: string;
    filename: string;
    content_type: string;
    size: number;
    sha256: string;
  }>;
  signals: { dwell_ms?: number | null; webdriver?: boolean };
  accepted: boolean;
  reason: string;
};

type Posting = {
  ats: string;
  job: string;
  variant: string;
  path: string;
  company: string;
};

async function call<T>(path: string, method = "GET"): Promise<T> {
  if (!atsFormsUrl) throw new Error("set E2E_ATS_FORMS_URL");
  const response = await fetch(atsFormsUrl + path, { method });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status}`);
  return (await response.json()) as T;
}

export const stub = {
  reset: () => call<{ ok: boolean }>("/_stub/reset", "POST"),
  submissions: () => call<Submission[]>("/_stub/submissions"),
  mailbox: () =>
    call<Array<{ submission: string; to: string; code: string }>>(
      "/_stub/mailbox",
    ),
  file: async (sha: string): Promise<Buffer> => {
    const response = await fetch(`${atsFormsUrl}/_stub/files/${sha}`);
    return Buffer.from(await response.arrayBuffer());
  },
  posting: async (ats: string, variant = "", job?: string): Promise<string> => {
    const rows = await call<Posting[]>("/_stub/postings");
    const found = rows.find(
      (r) =>
        r.ats === ats &&
        r.variant === variant &&
        (job === undefined || r.job === job),
    );
    if (!found) throw new Error(`no ${ats} posting for variant "${variant}"`);
    return `${atsFormsUrl ?? ""}${found.path}`;
  },
};

/** A browser that does not announce itself as automated. */
export async function unflagged(context: BrowserContext) {
  await context.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, "webdriver", {
      get: () => false,
    });
  });
}

export const pdfFile = (name: string) => ({
  name,
  mimeType: "application/pdf",
  buffer: PDF,
});

type Where = Page | FrameLocator;

/** The field whose label starts with `label` (an anchored match: "Country" is not "...the country in..."). */
const fieldOf = (where: Where, label: string): Locator =>
  where.locator(".field").filter({
    has: where.locator("label").filter({
      hasText: new RegExp(
        `^\\s*${label.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}`,
      ),
    }),
  });

export const ashby = {
  field: fieldOf,
  async text(where: Where, label: string, value: string) {
    await ashby
      .field(where, label)
      .locator("input, textarea")
      .first()
      .fill(value);
  },
  async yesNo(where: Where, label: string, answer: "yes" | "no") {
    await ashby
      .field(where, label)
      .locator(
        `.ashby-application-form-input-yesno button[data-option=${answer}]`,
      )
      .click();
  },
  async location(where: Where, typed: string, option: string) {
    const input = ashby.field(where, "Current location").locator("input");
    await input.pressSequentially(typed);
    await where.getByRole("option", { name: option }).click();
  },
  async upload(where: Where, label: string, name: string) {
    await ashby
      .field(where, label)
      .locator("input[type=file]")
      .setInputFiles(pdfFile(name));
  },
  async fillAll(where: Where) {
    await ashby.text(where, "Full name", "Alex Example");
    await ashby.text(where, "Email", "e2e-alex@example.org");
    await ashby.location(where, "par", "Paris, France");
    await ashby.upload(where, "Resume", "cv.pdf");
    await ashby.yesNo(where, "Are you legally authorised", "yes");
    await ashby.yesNo(where, "Will you now", "no");
    await ashby.text(
      where,
      "Why Example Robotics?",
      "I build agents that run operations.",
    );
    await ashby
      .field(where, "I agree to the processing")
      .locator("input")
      .check();
  },
};

export const greenhouse = {
  field: fieldOf,
  async text(where: Where, label: string, value: string) {
    await greenhouse.field(where, label).locator("input").first().fill(value);
  },
  /** A react-select combobox: open it, type to filter, click the option. */
  async choose(where: Where, label: string, option: string) {
    const field = greenhouse.field(where, label);
    await field.locator(".select__control").click();
    await field
      .locator("input.select__input")
      .pressSequentially(option.slice(0, 3));
    await field.locator(".select__option", { hasText: option }).first().click();
  },
  async check(where: Where, label: string) {
    await greenhouse
      .field(where, label)
      .locator("input[type=checkbox]")
      .check();
  },
  async fillAll(where: Where) {
    await greenhouse.text(where, "First Name", "Alex");
    await greenhouse.text(where, "Last Name", "Example");
    await greenhouse.text(where, "Email", "e2e-alex@example.org");
    await greenhouse
      .field(where, "Resume/CV")
      .locator("input[type=file]")
      .setInputFiles(pdfFile("cv.pdf"));
    await greenhouse.choose(where, "Are you legally authorized", "Yes");
    await greenhouse.choose(where, "Country", "France");
    await greenhouse.check(where, "I have read");
  },
};
