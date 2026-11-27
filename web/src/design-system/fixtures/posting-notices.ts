import type { Notice } from "@/features/jobs/api/notices-api";

import { POSTINGS } from "./postings";

/** What a posting says before the click, as the api words it (api/app/jobs/notices.py). */
export const NOTICES: Notice[] = [
  {
    kind: "signal",
    code: "no_sponsorship",
    text: "This posting says it does not sponsor visas, and your profile says you need sponsorship in Germany.",
    quote: "We are unable to offer visa sponsorship for this role.",
    level: "warning",
    job_id: null,
  },
  {
    kind: "closing",
    code: "closes_on",
    text: "This posting closes on 14 November 2026, in 4 days.",
    quote: "Closing date: 14 November 2026.",
    level: "warning",
    job_id: null,
  },
  {
    kind: "cross_listing",
    code: "same_text",
    text: "Its text is nearly the same as Data engineer at Example Talent, which you applied to on 2 November 2026.",
    quote: null,
    level: "warning",
    job_id: POSTINGS[1]?.id ?? null,
  },
  {
    kind: "signal",
    code: "degree",
    text: "This posting asks for a degree.",
    quote: "Master's degree in computer science or equivalent.",
    level: "info",
    job_id: null,
  },
  {
    kind: "history",
    code: "applied_before",
    text: "You applied to Lumen Freight on 12 September 2026 for Analytics engineer, with no reply recorded in 59 days.",
    quote: null,
    level: "info",
    job_id: POSTINGS[2]?.id ?? null,
  },
];

/** Jobs J35: what the api says of the languages a posting asks for, against the profile's. */
export const LANGUAGE_NOTICES: Notice[] = [
  {
    kind: "signal",
    code: "language_missing",
    text: "This posting requires German, which your profile does not list.",
    quote: "Verhandlungssichere Deutschkenntnisse",
    level: "warning",
    job_id: null,
  },
  {
    kind: "signal",
    code: "language_level",
    text: "This posting asks for English at “fluent”; your profile lists Anglais at “B2”.",
    quote: "Fluent English in a team of twelve",
    level: "warning",
    job_id: null,
  },
  {
    kind: "signal",
    code: "language_unread",
    text: "This posting requires Dutch; your profile lists “Plattdüütsch”, which the app cannot read as a language.",
    quote: "Fluent Dutch required",
    level: "warning",
    job_id: null,
  },
];
