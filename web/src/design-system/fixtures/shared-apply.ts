import { delay, http, HttpResponse } from "msw";

import type {
  Answer,
  Application,
  ApplicationEvent,
  Approval,
  Chosen,
  Filled,
  Question,
} from "@/features/apply/api/apply-api";
import type {
  BulkPreview,
  BulkStarted,
} from "@/features/apply/api/bulk-apply-api";
import type { EmailApplication } from "@/features/apply/api/email-application-api";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { APP_ID, fakeAnswer, fakeApplication } from "@/test/apply";
import { server } from "@/test/server";

import { FILLED } from "./applications";
import { POSTING_DETAIL, POSTINGS } from "./postings";

/** The posting of the apply stories: Lumen Freight's Senior data engineer, on an invented form. */
export const APPLY_JOB: JobDetail = POSTING_DETAIL;
export const FORM_URL = "https://jobs.example.org/lumen-freight/apply";

/** An application of APPLY_JOB in `state`, Review first unless `mode` says otherwise. */
export function applicationIn(
  state: string,
  overrides: Partial<Application> = {},
): Application {
  return fakeApplication(state, {
    posting_id: APPLY_JOB.id,
    form_url: FORM_URL,
    created_at: "2026-11-05T08:00:00Z",
    updated_at: "2026-11-05T08:01:12Z",
    ...overrides,
  });
}

const at = (time: string) => `2026-11-05T${time}Z`;

/** A Review first application from the Apply click to the site's confirmation. */
export const SUBMITTED_EVENTS: ApplicationEvent[] = [
  { at: at("08:00:00"), kind: "created", data: {} },
  { at: at("08:00:04"), kind: "lease", data: {} },
  { at: at("08:00:41"), kind: "saved_answer_used", data: {} },
  { at: at("08:01:02"), kind: "filled", data: {} },
  { at: at("08:01:03"), kind: "screenshot", data: { kind: "before" } },
  { at: at("08:06:30"), kind: "decision", data: {} },
  { at: at("08:07:10"), kind: "click", data: {} },
  { at: at("08:07:12"), kind: "screenshot", data: { kind: "after" } },
  { at: at("08:07:13"), kind: "ended", data: {} },
];

/** The site showed errors after the click. */
export const REFUSED_EVENTS: ApplicationEvent[] = [
  ...SUBMITTED_EVENTS.slice(0, 7),
  { at: at("08:07:12"), kind: "screenshot", data: { kind: "after" } },
  { at: at("08:07:13"), kind: "refused", data: {} },
];

/** A run that was tried three times, with an event kind this build does not know yet. */
export const LONG_EVENTS: ApplicationEvent[] = [
  { at: at("08:00:00"), kind: "created", data: {} },
  { at: at("08:00:02"), kind: "waiting", data: {} },
  { at: at("08:03:40"), kind: "lease", data: {} },
  { at: at("08:04:10"), kind: "screenshot_failed", data: {} },
  { at: at("08:04:12"), kind: "retry", data: {} },
  { at: at("08:20:00"), kind: "lease", data: {} },
  { at: at("08:20:31"), kind: "saved_answer_used", data: {} },
  { at: at("08:20:33"), kind: "saved_answer_used", data: {} },
  { at: at("08:21:02"), kind: "filled", data: {} },
  { at: at("08:21:03"), kind: "screenshot", data: { kind: "before" } },
  { at: at("09:02:30"), kind: "decision", data: {} },
  { at: at("09:02:31"), kind: "submit_stopped", data: {} },
  { at: at("09:02:32"), kind: "retry", data: {} },
  { at: at("09:15:00"), kind: "decision", data: {} },
  { at: at("09:15:40"), kind: "click", data: {} },
  { at: at("09:15:42"), kind: "screenshot", data: { kind: "after" } },
  { at: at("09:15:43"), kind: "cover_letter_uploaded_twice", data: {} },
  { at: at("09:15:44"), kind: "ended", data: {} },
];

/** The CV and letter sent with the form. */
export const DOCUMENTS: Approval["documents"] = {
  cv: { version_id: "v-cv", number: 3, pages: 1, corrections: 0 },
  letter: { version_id: "v-letter", number: 2, pages: 1, corrections: 0 },
};

/** The filled form waiting for the person's approval, with `answers`. */
export function approvalOf(
  answers: Answer[] = FILLED,
  overrides: Partial<Approval> = {},
): Approval {
  return {
    state: "ready",
    dry_run: false,
    answers_hash: "hash-1",
    answers,
    documents: DOCUMENTS,
    screenshots: [
      { id: "shot-before", kind: "before", created_at: at("08:01:03") },
    ],
    ...overrides,
  };
}

/** The same form with an optional question left empty. */
export const OPTIONAL_LEFT: Answer[] = [
  ...FILLED,
  fakeAnswer("referral", "Who referred you?", null, {
    source: "left",
    you_answer: true,
  }),
  fakeAnswer("portfolio", "Portfolio", null, {
    kind: "file",
    source: "left",
    you_answer: true,
  }),
];

const EMAIL_BODY = `Hello,

I am applying for the Senior data engineer role at Lumen Freight. I have run Kafka pipelines in production for four years, and I moved our nightly batch jobs to dbt last spring.

My CV and a cover letter are attached.

Kind regards,
Zoë Ångström`;

const CLAIM = "I have run Kafka pipelines in production for four years";
const CLAIM_START = EMAIL_BODY.indexOf(CLAIM);

/** An application by e-mail: the recipient, the subject and the body are answers of the form. */
export function emailApproval(
  options: {
    to?: string | null;
    writtenBy?: "person" | "agent";
    claims?: boolean;
  } = {},
): Approval {
  const { to = "jobs@lumen-freight.example.org", writtenBy, claims } = options;
  return approvalOf(
    [
      fakeAnswer("email_to", "To", to, { source: "document" }),
      fakeAnswer(
        "email_subject",
        "Subject",
        "Application: Senior data engineer",
        { source: "document" },
      ),
      fakeAnswer("email_body", "Message", EMAIL_BODY, {
        kind: "textarea",
        source: writtenBy === "agent" ? "agent" : "document",
      }),
    ],
    {
      email: {
        written_by: writtenBy ?? "person",
        issues: claims
          ? [
              {
                id: "claim-1",
                code: "unsupported_claim",
                start: CLAIM_START,
                end: CLAIM_START + CLAIM.length,
              },
            ]
          : [],
      },
    },
  );
}

/** The application e-mail as GET /api/applications/:id/email answers it. */
export function emailDraft(
  overrides: Partial<EmailApplication> = {},
): EmailApplication {
  return {
    id: APP_ID,
    status: "draft",
    to: ["jobs@lumen-freight.example.org"],
    subject: "Application: Senior data engineer",
    body: EMAIL_BODY,
    send_id: null,
    message: null,
    issues: [],
    ...overrides,
  };
}

export const EMAIL_CLAIM = {
  code: "unsupported_claim",
  start: CLAIM_START,
  end: CLAIM_START + CLAIM.length,
};

/** Serves the application's e-mail, or fails reading it; "Check Sent" answers `reconcile`. */
export function fakeEmailApplication(
  email: EmailApplication,
  reconcile: () => Response = () => HttpResponse.json({ resolved: 0 }),
) {
  server.use(
    http.get("*/api/applications/:id/email", () => HttpResponse.json(email)),
    http.post("*/api/mail/sends/:id/reconcile", async () => {
      await delay(600);
      return reconcile();
    }),
  );
}

/** Every field the apply agent filled in Review first (A20), by step, from its own acts. */
export const AGENT_FILLED: Filled = {
  fields: [
    { step: "Your details", label: "First name", kind: "text", value: "Zoë" },
    {
      step: "Your details",
      label: "Last name",
      kind: "text",
      value: "Ångström",
    },
    {
      step: "Your details",
      label: "Email",
      kind: "text",
      value: "friend@example.org",
    },
    { step: "Your details", label: "Resume", kind: "file", value: "cv.pdf" },
    {
      step: "Experience",
      label: "Company",
      kind: "text",
      value: "Northwind Rail",
    },
    {
      step: "Experience",
      label: "Company",
      kind: "text",
      value: "Atelier Brume",
    },
    {
      step: "Questions",
      label: "Are you allowed to work in France without a visa?",
      kind: "choice",
      value: "Yes",
    },
    {
      step: "Questions",
      label: "How did you hear about us?",
      kind: "choice",
      value: "Company website",
      chosen:
        "No saved answer matched, and the posting was found on the company's careers page.",
    },
    {
      step: "Questions",
      label: "I agree to the privacy notice",
      kind: "tick",
      value: "Ticked",
    },
    {
      step: "Questions",
      label: "Anything else you want to tell us?",
      kind: "text",
      value: "",
    },
  ],
  submit: "Submit application",
  fields_hash: "fields-1",
};

/** The same list with long answers, as a work history or a cover letter box gives. */
export const AGENT_FILLED_LONG: Filled = {
  ...AGENT_FILLED,
  fields: [
    ...AGENT_FILLED.fields,
    {
      step: "Experience",
      label:
        "Describe a data pipeline you built and still run, and what you would change today",
      kind: "text",
      value:
        "At Northwind Rail I built the pipeline that turns ticket scans into hourly ridership figures: Kafka topics per station, a dbt project of about ninety models, and Airflow for the nightly rebuild. It has run for three years with two incidents, both from upstream schema changes.\n\nToday I would add data contracts at the producer side, so a renamed field fails the producer's build instead of our morning dashboard.",
    },
  ],
};

/** One click Apply's answers the assistant chose for the person (A20), with its reason. */
export const CHOSEN: Chosen[] = [
  {
    label: "How did you hear about us?",
    value: "Company website",
    why: "No saved answer matched, and the posting was found on the company's careers page.",
  },
  {
    label: "Salary expectations",
    value: "55000 EUR a year",
    why: "The low end of the posting's range, as no saved answer matched.",
  },
  {
    label: "Earliest start date",
    value: "1 February 2027",
    why: "Your notice period of one month, from the end of December.",
  },
];

/** One click Apply's open question on a fact (A19), in the api's fixed sentence. */
export const FACT_QUESTION: Question = {
  sentence:
    'The assistant needs your answer for "Notice period *" on jobs.example.org.',
  label: "Notice period *",
  host: "jobs.example.org",
  kind: "fact",
};

/** The same, on something only the person can do on the site. */
export const ACTION_QUESTION: Question = {
  sentence:
    'The assistant needs you on jobs.example.org for "Verify you are human". Do it there, then go on.',
  label: "Verify you are human",
  host: "jobs.example.org",
  kind: "action",
};

/** A question with a long label, as some forms write their questions. */
export const LONG_QUESTION: Question = {
  sentence:
    'The assistant needs your answer for "Please describe, in your own words, a situation where you had to explain a technical tradeoff to people outside engineering, and what you learned from it *" on careers.lumen-freight.example.org.',
  label:
    "Please describe, in your own words, a situation where you had to explain a technical tradeoff to people outside engineering, and what you learned from it *",
  host: "careers.lumen-freight.example.org",
  kind: "fact",
};

/** POST /api/applications/:id/question: the application goes on, or the api refuses. */
export function fakeQuestionAnswer(
  respond: () => Response | Promise<Response> = () =>
    HttpResponse.json(applicationIn("queued", { mode: "direct" })),
) {
  server.use(http.post("*/api/applications/:id/question", respond));
}

/** "Remember this answer for next forms": saved, or kept because one was saved already. */
export function fakeRemember(outcome: "saved" | "kept" | "error" = "saved") {
  server.use(
    http.post("*/api/applications/:id/remember", async ({ request }) => {
      await delay(300);
      if (outcome === "error")
        return HttpResponse.json(
          { detail: "Saved answers are full: remove one in Settings first." },
          { status: 409 },
        );
      const body = (await request.json()) as { field_key: string };
      return HttpResponse.json({ outcome, question: body.field_key });
    }),
  );
}

// --- Apply to the selected postings ------------------------------------------------------------

const ESTIMATE = {
  tokens: { low: 9000, high: 14000 },
  cost: { currency: "USD", low: "0.03", high: "0.05" },
};

const posting = (index: number) => {
  const job = POSTINGS[index];
  if (!job) throw new Error(`No posting ${String(index)} in POSTINGS.`);
  return { posting_id: job.id, company: job.company, title: job.title };
};

/** What one click on "Apply to N" would do for four selected postings, the review box unticked. */
export function bulkPreview(overrides: Partial<BulkPreview> = {}): BulkPreview {
  return {
    review_before_send: false,
    limit: 20,
    items: [
      {
        ...posting(0),
        documents: [],
        estimate: null,
        email: false,
        seen: "s0",
      },
      {
        ...posting(1),
        documents: ["letter"],
        estimate: ESTIMATE,
        email: false,
        seen: "s1",
      },
      {
        ...posting(2),
        documents: ["cv", "letter"],
        estimate: {
          tokens: { low: 21000, high: 30000 },
          cost: { currency: "USD", low: "0.07", high: "0.11" },
        },
        email: false,
        seen: "s2",
      },
      { ...posting(3), documents: [], estimate: null, email: true, seen: "s3" },
    ],
    left_out: [],
    beyond: [],
    estimate: {
      tokens: { low: 30000, high: 44000 },
      cost: { currency: "USD", low: "0.10", high: "0.16" },
    },
    cost_note: "Prices are the provider's public rates; your bill may differ.",
    ...overrides,
  };
}

/** Selected postings the click leaves out, each with the api's reason. */
export const LEFT_OUT: BulkPreview["left_out"] = [
  {
    ...posting(4),
    code: "already_applied",
    detail: "You already applied to this posting on 2 November.",
  },
  {
    ...posting(5),
    code: "closed",
    detail: "This posting is closed.",
  },
  {
    posting_id: "00000000-0000-4000-9000-000000000999",
    company: null,
    title: null,
    code: "not_found",
    detail: "This posting is no longer in your list.",
  },
];

/** The answer of "Apply to N": every posting started, or some left out since the preview. */
export function bulkStarted(preview: BulkPreview, refused = 0): BulkStarted {
  const results = preview.items.map((item, index) =>
    index < preview.items.length - refused
      ? {
          posting_id: item.posting_id,
          started: true,
          application_id: `app-${String(index)}`,
          mode: item.email ? "review" : "direct",
          state: "queued",
        }
      : {
          posting_id: item.posting_id,
          started: false,
          code: "settings_paused",
          detail:
            "Automatic applications were paused since the list was read, so it was not started.",
        },
  );
  return {
    results,
    started: preview.items.length - refused,
    left_out: refused,
  };
}

/** The bulk preview and start routes: `preview` and `start` answer each call. */
export function fakeBulkApply({
  preview = () => HttpResponse.json(bulkPreview()),
  start = () => HttpResponse.json(bulkStarted(bulkPreview())),
}: {
  preview?: () => Response | Promise<Response>;
  start?: () => Response | Promise<Response>;
} = {}) {
  server.use(
    http.post("*/api/apply/bulk/preview", async () => {
      await delay(400);
      return preview();
    }),
    http.post("*/api/apply/bulk", async () => {
      await delay(800);
      return start();
    }),
  );
}

// --- Likes ---------------------------------------------------------------------------------------

/**
 * POST /api/postings/:id/like for each posting: the application it starts in `mode`, a rehearsal
 * when `dryRun`, or the api's refusal for the ids in `refused`.
 */
export function fakeLikes({
  mode = "review",
  dryRun = false,
  refused = {},
  wait = 300,
}: {
  mode?: string;
  dryRun?: boolean;
  refused?: Record<string, string>;
  wait?: number | "infinite";
} = {}) {
  server.use(
    http.post("*/api/postings/:id/like", async ({ params }) => {
      await delay(wait);
      const id = String(params.id);
      const why = refused[id];
      if (why)
        return HttpResponse.json(
          { detail: why, code: "refused" },
          { status: 409 },
        );
      return HttpResponse.json({
        application: applicationIn("queued", {
          posting_id: id,
          mode,
          dry_run: dryRun,
        }),
        documents: [],
      });
    }),
  );
}
