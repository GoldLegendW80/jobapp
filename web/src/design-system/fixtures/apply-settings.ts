import { http, HttpResponse } from "msw";

import type {
  ApplySettings,
  Bank,
  Cap,
} from "@/features/apply/api/settings-api";
import { defaultApplySettings, server } from "@/test/server";

/**
 * The Applications settings api in memory: GET and PUT of the settings, the company caps and the
 * saved answers, so a story's switches and boxes save and answer as the api does.
 */
export function fakeApplySettings(
  initial: Partial<ApplySettings> = {},
  caps: Omit<Cap, "id">[] = [],
  answers: Omit<Bank["items"][number], "used_count" | "updated_at">[] = [],
) {
  const state = {
    settings: { ...defaultApplySettings, ...initial },
    caps,
    answers,
    revision: 1,
  };
  const capsBody = () => ({
    items: state.caps.map((cap, index) => ({
      id: `cap-${String(index + 1)}`,
      ...cap,
    })),
    revision: String(state.revision).padStart(64, "0"),
  });
  const bankBody = () => ({
    items: state.answers.map((entry) => ({
      ...entry,
      used_count: 2,
      updated_at: "2026-11-02T08:00:00Z",
    })),
  });
  server.use(
    http.get("*/api/apply/settings", () => HttpResponse.json(state.settings)),
    http.put("*/api/apply/settings", async ({ request }) => {
      const body = (await request.json()) as Partial<ApplySettings>;
      state.settings = {
        ...state.settings,
        ...body,
        min_fit_chosen: state.settings.min_fit_chosen || "min_fit" in body,
      };
      return HttpResponse.json(state.settings);
    }),
    http.get("*/api/apply/caps", () => HttpResponse.json(capsBody())),
    http.put("*/api/apply/caps", async ({ request }) => {
      const body = (await request.json()) as {
        items: Omit<Cap, "id" | "used">[];
      };
      state.caps = body.items.map((item) => ({
        ...item,
        used: state.caps.find((one) => one.label === item.label)?.used ?? 0,
      }));
      state.revision += 1;
      return HttpResponse.json(capsBody());
    }),
    http.get("*/api/apply/answer-bank", () => HttpResponse.json(bankBody())),
    http.put("*/api/apply/answer-bank", async ({ request }) => {
      const body = (await request.json()) as {
        question_key: string;
        answer: string | null;
      };
      state.answers =
        body.answer === null
          ? state.answers.filter(
              (one) => one.question_key !== body.question_key,
            )
          : state.answers.map((one) =>
              one.question_key === body.question_key
                ? { ...one, answer: body.answer }
                : one,
            );
      return HttpResponse.json(bankBody());
    }),
  );
  return state;
}

/** Two invented companies the person limited, one with a rule for the same role. */
export const CAPS: Omit<Cap, "id">[] = [
  {
    label: "Lumen Freight",
    max_count: 2,
    window_days: 90,
    same_role_days: 180,
    used: 1,
  },
  {
    label: "Kestrel Health",
    max_count: 3,
    window_days: 30,
    same_role_days: null,
    used: 0,
  },
];

/** Answers the person saved from earlier forms. */
export const SAVED_ANSWERS = [
  {
    question_key: "notice_period",
    question: "What is your notice period?",
    answer: "One month",
  },
  {
    question_key: "work_permit_fr",
    question: "Are you allowed to work in France without a visa?",
    answer: "Yes",
  },
  {
    question_key: "start_date",
    question: "When could you start?",
    answer: "From 5 January 2027",
  },
];
