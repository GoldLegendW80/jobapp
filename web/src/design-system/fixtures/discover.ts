import { makeCard } from "@/test/deck";
import type { Card } from "@/features/opportunities/api/opportunities-api";

import { POSTINGS } from "./postings";

const PAY: Record<string, string> = {
  year: "a year",
  day: "a day",
  month: "a month",
};

/** The invented postings of POSTINGS as deck cards, best fit first, each with its reason. */
export const DECK: Card[] = POSTINGS.filter(
  (job) => job.fit !== null && job.fit >= 58,
)
  .sort((a, b) => (b.fit ?? 0) - (a.fit ?? 0))
  .map((job, index) =>
    makeCard(index + 1, {
      id: job.id,
      title: job.title,
      company: job.company,
      location: job.location,
      contract_type: job.contract_type,
      source: `greenhouse:${job.company.toLowerCase().replaceAll(" ", "-")}`,
      url: job.url,
      fit: job.fit,
      found_on: job.found_on,
      work_mode: job.work_mode,
      pay:
        job.pay_min && job.pay_max && job.pay_period
          ? `${job.pay_min.toLocaleString("en")} to ${job.pay_max.toLocaleString("en")} ${job.pay_currency ?? "EUR"} ${PAY[job.pay_period] ?? ""}`.trim()
          : null,
      reason: {
        matches: job.competencies,
        gap: job.gaps[0] ?? null,
        text: null,
      },
    }),
  );
