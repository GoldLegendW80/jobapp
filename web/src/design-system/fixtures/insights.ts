import type { Insights } from "@/features/jobs/api/insights-api";

/** Invented numbers of a search well under way: every floor reached, two silent applications. */
export const INSIGHTS: Insights = {
  silent_days: 60,
  funnel: {
    found: 412,
    liked: 96,
    applied: 74,
    answered: 31,
    interview: 12,
    rejected: 19,
    passed: 140,
    waiting: 38,
    answer_rate: 42,
    interview_rate: 16,
  },
  by_fit: [
    { key: "80_plus", applied: 28, answered: 15, interviews: 8, rate: 29 },
    { key: "60_79", applied: 33, answered: 12, interviews: 4, rate: 12 },
    { key: "under_60", applied: 9, answered: 4, interviews: 0, rate: 0 },
    { key: "none", applied: 4, answered: 0, interviews: 0, rate: null },
  ],
  by_ats: [
    { key: "greenhouse", applied: 26, answered: 13, interviews: 6, rate: 23 },
    { key: "ashby", applied: 21, answered: 10, interviews: 4, rate: 19 },
    { key: "workday", applied: 12, answered: 1, interviews: 0, rate: 0 },
    { key: "lever", applied: 6, answered: 3, interviews: 2, rate: null },
    { key: "other", applied: 9, answered: 4, interviews: 0, rate: 9 },
  ],
  by_source: [
    { key: "linkedin", applied: 30, answered: 11, interviews: 4, rate: 13 },
    { key: "ashby", applied: 18, answered: 9, interviews: 4, rate: 22 },
    { key: "mail", applied: 14, answered: 7, interviews: 3, rate: 21 },
    { key: "manual", applied: 7, answered: 3, interviews: 1, rate: null },
    { key: "remoteok", applied: 5, answered: 1, interviews: 0, rate: null },
  ],
  first_answer: { median_days: 9, measured: 27, undated: 4, waiting: 38 },
  pass_reasons: [
    { reason: "pay", count: 41 },
    { reason: "remote", count: 22 },
    { reason: "stack", count: 9 },
  ],
  passes_without_reason: 68,
  silent: [
    {
      id: "00000000-0000-4000-a000-000000000101",
      company: "Example Robotics",
      title: "Applied AI Engineer",
      applied_on: "2026-07-21",
      last_signal: "2026-07-21",
      days: 109,
    },
    {
      id: "00000000-0000-4000-a000-000000000102",
      company: "Example Labs",
      title: "Machine Learning Engineer",
      applied_on: "2026-08-02",
      last_signal: "2026-08-30",
      days: 69,
    },
  ],
  silent_undated: 1,
};

/** Invented numbers of a search just started: under every floor, nothing silent. */
export const FEW_INSIGHTS: Insights = {
  silent_days: 60,
  funnel: {
    found: 37,
    liked: 6,
    applied: 4,
    answered: 1,
    interview: 0,
    rejected: 1,
    passed: 12,
    waiting: 3,
    answer_rate: null,
    interview_rate: null,
  },
  by_fit: [
    { key: "80_plus", applied: 3, answered: 1, interviews: 0, rate: null },
    { key: "60_79", applied: 1, answered: 0, interviews: 0, rate: null },
    { key: "under_60", applied: 0, answered: 0, interviews: 0, rate: null },
    { key: "none", applied: 0, answered: 0, interviews: 0, rate: null },
  ],
  by_ats: [
    { key: "ashby", applied: 3, answered: 1, interviews: 0, rate: null },
    { key: "unknown", applied: 1, answered: 0, interviews: 0, rate: null },
  ],
  by_source: [
    { key: "linkedin", applied: 4, answered: 1, interviews: 0, rate: null },
  ],
  first_answer: { median_days: null, measured: 1, undated: 0, waiting: 3 },
  pass_reasons: [],
  passes_without_reason: 12,
  silent: [],
  silent_undated: 0,
};

/** No posting at all. */
export const NO_INSIGHTS: Insights = {
  ...FEW_INSIGHTS,
  funnel: {
    ...FEW_INSIGHTS.funnel,
    found: 0,
    liked: 0,
    applied: 0,
    answered: 0,
    rejected: 0,
    passed: 0,
    waiting: 0,
  },
  by_fit: [],
  by_ats: [],
  by_source: [],
  passes_without_reason: 0,
};
