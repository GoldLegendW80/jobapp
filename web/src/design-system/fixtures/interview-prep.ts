import { http, HttpResponse } from "msw";

import type {
  InterviewPrep,
  PrepContent,
} from "@/features/documents/api/interview-prep-api";
import type { Consent } from "@/features/documents/api/consent-api";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { fakeDocuments } from "@/test/documents";
import { fakeJobDetail } from "@/test/jobs";
import { server } from "@/test/server";

/** An invented preparation for the page stories: Lumen Freight hires a data engineer. */
export const PREP_CONTENT: PrepContent = {
  schema: 1,
  target: "Senior data engineer at Lumen Freight",
  expected: [
    {
      question: "Walk me through a pipeline you ran in production.",
      basis: "posting",
      round: "hiring_manager",
      why: "The posting asks for someone who owns the freight data pipelines.",
    },
    {
      question: "Why Lumen Freight, and why now?",
      basis: "posting",
      round: "recruiter",
      why: "Every first call asks it.",
    },
    {
      question: "Which part of the pricing model did you build yourself?",
      basis: "hiring_panel",
      round: "hiring_manager",
      why: "The hiring panel found the CV's pricing line vague.",
    },
  ],
  gaps: [
    {
      gap: "The posting asks for Kafka; your profile does not name it.",
      answer:
        "Say you have not run Kafka in production yet, and describe the batch pipelines you ran at Northwind Analytics.",
    },
  ],
  stories: [
    {
      title: "The freight pricing pipeline",
      situation:
        "At Northwind Analytics prices were computed by hand each week.",
      task: "Give the sales team prices they could trust every morning.",
      action: "I built the nightly pipeline in Python and dbt.",
      result: "Prices reached the sales team before 8 every morning.",
    },
    {
      title: "Data quality checks",
      situation: "Reports broke without warning.",
      task: "Catch broken data before the reports.",
      action: "I added checks to every model of the pipeline.",
      result: "Broken loads stopped before reaching a report.",
    },
  ],
  ask: [
    "How will you measure the success of this role after six months?",
    "Who uses the pipelines today, and what do they ask for most?",
  ],
  debrief: [],
};

const DONE = {
  status: "done" as const,
  error: null,
  created_at: "2026-11-07T09:00:00Z",
};

/** A posting prepared once, with no practice yet. */
export function preparedFor(
  content: Partial<PrepContent> = {},
  rest: Partial<InterviewPrep> = {},
): InterviewPrep {
  return {
    document: {
      id: "prep-doc",
      draft: { ...PREP_CONTENT, ...content },
      draft_revision: 3,
      notes: [],
      language: "en",
      versions: 1,
    },
    writing: DONE,
    practice: [],
    practice_open: false,
    stories_proposal: null,
    ...rest,
  };
}

export const NOT_PREPARED: InterviewPrep = {
  document: null,
  writing: null,
  practice: [],
  practice_open: false,
  stories_proposal: null,
};

/**
 * The interview preparation of `job` served from a state the requests change: Prepare (refused
 * once without the consent, then written at once), a practice message and its answer, the kept
 * stories as a proposal accepted or rejected, and a Debrief saved by hand.
 */
export function fakeInterviewPrep(
  job: JobDetail,
  initial: InterviewPrep,
  options: { consent?: Partial<Consent> } = {},
) {
  fakeJobDetail(job);
  const docs = fakeDocuments({ consent: options.consent });
  let state: InterviewPrep = initial;
  server.use(
    http.get("*/api/jobs/:id/interview-prep", () => HttpResponse.json(state)),
    http.post("*/api/jobs/:id/interview-prep", () => {
      if (!docs.consent.accepted)
        return HttpResponse.json(
          {
            detail:
              "Confirm what is sent to the model first, then click again.",
          },
          { status: 409 },
        );
      state = preparedFor();
      return HttpResponse.json(
        { document_id: "prep-doc", writing: DONE },
        { status: 202 },
      );
    }),
    http.post("*/api/documents/:id/practice", async ({ request }) => {
      const body = (await request.json()) as { text: string };
      const at = "2026-11-07T09:05:00Z";
      const asked = {
        id: state.practice.length,
        role: "user" as const,
        text: body.text,
        at,
      };
      state = {
        ...state,
        practice: [
          ...state.practice,
          asked,
          {
            id: state.practice.length + 1,
            role: "assistant",
            text: "Hiring manager round: walk me through a pipeline you ran in production.",
            at,
          },
        ],
      };
      return HttpResponse.json({ message: asked }, { status: 202 });
    }),
    http.post("*/api/documents/:id/stories", () => {
      state = { ...state, stories_proposal: "stories-proposal" };
      return HttpResponse.json({ proposal_id: "stories-proposal" });
    }),
    http.post("*/api/proposals/:id/:decision", ({ params }) => {
      state = { ...state, stories_proposal: null };
      return HttpResponse.json({
        id: "stories-proposal",
        run_id: null,
        target: "profile",
        target_id: null,
        kind: "profile_item",
        payload: {},
        status: params.decision === "accept" ? "accepted" : "rejected",
        decided_by: "user",
        decided_at: "2026-11-07T09:10:00Z",
        reason: null,
        created_at: "2026-11-07T09:08:00Z",
      });
    }),
    http.put("*/api/documents/:id/draft", async ({ request }) => {
      const content = (await request.json()) as PrepContent;
      const document = state.document;
      if (document)
        state = {
          ...state,
          document: {
            ...document,
            draft: content,
            draft_revision: document.draft_revision + 1,
            versions: document.versions + 1,
          },
        };
      return HttpResponse.json({ draft_revision: 4 });
    }),
  );
  return docs;
}
