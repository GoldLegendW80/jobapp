import { http, HttpResponse } from "msw";

import type { Answer } from "@/features/apply/api/apply-api";
import type { QueueItem } from "@/features/apply/api/queue-api";
import { fakeAnswer, leftAnswer } from "@/test/apply";
import { queueItem } from "@/test/apply-queue";
import { server } from "@/test/server";

import { POSTINGS } from "./postings";

/**
 * Application `n` of the review queue for posting `posting` of POSTINGS, on an invented form, so
 * its title links to a believable job.
 */
export function application(
  n: number,
  state: string,
  posting: number,
  overrides: Partial<QueueItem> = {},
): QueueItem {
  const job = POSTINGS[posting];
  if (!job) throw new Error(`No posting ${String(posting)} in POSTINGS.`);
  return queueItem(n, state, {
    posting_id: job.id,
    title: job.title,
    company: job.company,
    form_url: `https://jobs.example.org/${job.company.toLowerCase().replaceAll(" ", "-")}/apply`,
    ...overrides,
  });
}

/** A form filled from the person's profile, documents and saved answers: nothing left. */
export const FILLED: Answer[] = [
  fakeAnswer("first_name", "First name", "Zoë", { required: true }),
  fakeAnswer("last_name", "Last name", "Ångström", { required: true }),
  fakeAnswer("email", "Email", "friend@example.org", { required: true }),
  fakeAnswer("resume", "Resume", "CV, version 3", {
    kind: "file",
    source: "document",
    required: true,
  }),
  fakeAnswer("cover_letter", "Cover letter", "Letter, version 2", {
    kind: "file",
    source: "document",
  }),
  fakeAnswer(
    "work_permit",
    "Are you allowed to work in France without a visa?",
    true,
    { kind: "yes_no", source: "answers", required: true },
  ),
  fakeAnswer("notice", "Notice period", "One month", { source: "bank" }),
];

/** The same form with one required question the app could not answer from the profile. */
export const ONE_LEFT: Answer[] = [
  ...FILLED,
  leftAnswer("why_us", "Why do you want to join Lumen Freight?", {
    required: true,
    remember: "off",
  }),
];

/** A filled form as the assistant saw it before the click: invented fields, no real site. */
const FORM_SHOT = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="640" viewBox="0 0 900 640">
<rect width="900" height="640" fill="#ffffff"/>
<rect width="900" height="56" fill="#f3f4f6"/>
<text x="32" y="36" font-family="sans-serif" font-size="20" font-weight="600" fill="#111827">Application form</text>
${[
  ["First name", "Zoë"],
  ["Last name", "Ångström"],
  ["Email", "friend@example.org"],
  ["Resume", "cv.pdf attached"],
  ["Notice period", "One month"],
]
  .map(
    ([label, value], index) =>
      `<text x="32" y="${String(100 + index * 84)}" font-family="sans-serif" font-size="14" fill="#4b5563">${label ?? ""}</text>
<rect x="32" y="${String(110 + index * 84)}" width="520" height="40" rx="6" fill="#ffffff" stroke="#d1d5db"/>
<text x="44" y="${String(136 + index * 84)}" font-family="sans-serif" font-size="15" fill="#111827">${value ?? ""}</text>`,
  )
  .join("\n")}
<rect x="32" y="560" width="160" height="44" rx="22" fill="#111827" stroke="#f97316" stroke-width="4"/>
<text x="112" y="588" text-anchor="middle" font-family="sans-serif" font-size="15" fill="#ffffff">Submit</text>
</svg>`;

/** Serves every application screenshot as the invented form above. */
export function fakeFormShots() {
  server.use(
    http.get(
      "*/api/applications/:id/screenshots/:shot",
      () =>
        new HttpResponse(FORM_SHOT, {
          headers: { "Content-Type": "image/svg+xml" },
        }),
    ),
  );
}

/** A moment from now, as the server's clock says it, for a Full auto hold. */
export const inMinutes = (minutes: number) =>
  new Date(Date.now() + minutes * 60_000).toISOString();
