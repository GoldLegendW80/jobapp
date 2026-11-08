import { http, HttpResponse } from "msw";

import type { Run } from "@/features/agents/api/runs-api";
import type {
  AssessmentResult,
  AssessmentView,
} from "@/features/jobs/api/assessment-api";
import { server } from "@/test/server";

import { SAVED_CREDENTIAL } from "./account";

/** An invented assessment of the Lumen Freight posting, as the agent and the code store it. */
export const ASSESSMENT_RESULT: AssessmentResult = {
  requirements: [
    {
      requirement: "Fluent French",
      importance: "critical",
      tier: "stated",
      signal: "French is required for the daily stand-up",
      match: "partial",
      profile_evidence: "Your profile lists French at B2.",
      interview_risk: "The first call may happen in French.",
      mitigation:
        "Say you work in French at B2 today and name the course you follow.",
    },
    {
      requirement: "Five years of Python",
      importance: "critical",
      tier: "stated",
      signal: "five years of Python is a must have",
      match: "strong",
      profile_evidence: "Six years of Python at Example Freight.",
      interview_risk: null,
      mitigation: null,
    },
    {
      requirement: "Kafka in production",
      importance: "high",
      tier: "structural",
      signal: "Listed under What you will do, twice",
      match: "missing",
      profile_evidence: "Not in your profile.",
      interview_risk: "A design question on event streams is likely.",
      mitigation:
        "Explain the queue you ran with RabbitMQ and how its ideas carry over.",
    },
    {
      requirement: "Talking with finance",
      importance: "meaningful",
      tier: "inferred",
      signal: null,
      match: "strong",
      profile_evidence: "You presented the cost report to finance each month.",
      interview_risk: null,
      mitigation: null,
    },
  ],
  more_requirements: 3,
  culture: [
    {
      criterion: "Hybrid work",
      finding: "fits",
      evidence: "Two days a week at the office in Lyon.",
    },
    {
      criterion: "No gambling companies",
      finding: "fits",
      evidence: "The company moves freight on rivers.",
    },
    {
      criterion: "Permanent contract",
      finding: "not_stated",
      evidence: "The posting does not say the kind of contract.",
    },
  ],
  signals: [
    {
      kind: "specificity",
      quote: "five people who own the data the rest of the company reads",
      note: "It names the team's size and what it owns.",
    },
  ],
  verdict:
    "A strong match on Python and data work, with Kafka the one gap to prepare.",
  confidence: "medium",
  role_clarity: {
    read: true,
    points: [
      {
        id: "supervisor",
        label: "Who the role reports to, and in which team",
        status: "missing",
        evidence: [],
      },
      {
        id: "scope",
        label: "What the role owns",
        status: "present",
        evidence: ["build and run the pipelines the team relies on"],
      },
      {
        id: "deliverables",
        label: "What it delivers, and in what form",
        status: "missing",
        evidence: [],
      },
      {
        id: "success",
        label: "How success is measured in the first months",
        status: "missing",
        evidence: [],
      },
      {
        id: "coordination",
        label: "Who it works with",
        status: "partial",
        evidence: ["work every week with the people who read your tables"],
      },
      {
        id: "cadence",
        label: "How often it delivers",
        status: "partial",
        evidence: ["work every week with the people who read your tables"],
      },
    ],
    questions: [
      "Who would this role report to, and in which department or team?",
      "What are the first concrete deliverables, in what form, and by when?",
      "How will success be measured in the first three to six months, and who judges it?",
      "Which teams or people would this role work with day to day?",
      "How often does the role deliver, and which deadlines are already fixed?",
    ],
  },
};

const RUN_ID = "77777777-7777-4777-8777-777777777777";

/** A posting's assessment view: assessed by run RUN_ID unless told otherwise. */
export function assessmentView(
  changes: Partial<AssessmentView> = {},
): AssessmentView {
  return {
    run: { id: RUN_ID, status: "done", error: null },
    assessment: {
      run_id: RUN_ID,
      created_at: "2026-11-07T08:30:00Z",
      result: ASSESSMENT_RESULT,
    },
    ...changes,
  };
}

export const NEVER_ASSESSED: AssessmentView = { run: null, assessment: null };
export const ASSESSING: AssessmentView = {
  run: { id: RUN_ID, status: "running", error: null },
  assessment: null,
};

/**
 * The assessment requests of a posting, a saved credential, and Assess answering a queued run.
 * Answers the ids of the postings Assess was clicked on.
 */
export function fakeAssessment(view: AssessmentView = assessmentView()) {
  let current = view;
  const starts: string[] = [];
  server.use(
    http.get("*/api/me/model-credential", () =>
      HttpResponse.json(SAVED_CREDENTIAL),
    ),
    http.get("*/api/jobs/:id/assessment", () => HttpResponse.json(current)),
    http.post("*/api/jobs/:id/assess", ({ params }) => {
      starts.push(String(params.id));
      const run: Run = {
        id: RUN_ID,
        agent: "jobs_assess",
        job_id: String(params.id),
        status: "queued",
        input: {},
        output: null,
        error: null,
        model: "claude-opus-5-5",
        input_tokens: null,
        output_tokens: null,
        cost_usd: null,
        cancel_requested: false,
        created_at: "2026-11-07T08:29:00Z",
        started_at: null,
        finished_at: null,
        duration_s: null,
      };
      current = {
        ...current,
        run: { id: RUN_ID, status: "queued", error: null },
      };
      return HttpResponse.json(run, { status: 202 });
    }),
  );
  return starts;
}
