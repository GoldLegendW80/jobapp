/**
 * The pure side of "Needs you" (apply A10a, needs-person.tsx): the headline and what to do for each
 * reason the api writes in `needs`, and the call of the app shell's dot. No dashes as separators.
 */
import { z } from "zod";

import { formatDateTime } from "@/features/jobs/format";
import { request } from "@/lib/api";

import type { Application, ApplicationEvent } from "./apply-api";
import { queueKey } from "./queue-api";

/** The steps the person takes in the live view while the assistant waits (api failures.WAIT_ON_PAGE). */
export const ON_PAGE = new Set(["captcha", "email_code", "forbidden"]);

export const PAYMENT_SENTENCE =
  "This site asked for a payment. Applications are free; this may not be a genuine posting.";

type Need = { title: string; what: string };

function siteOf(application: Application): string {
  try {
    return new URL(application.form_url).hostname || "the site";
  } catch {
    return "the site";
  }
}

/** The headline and what to do, for each reason the api writes in `needs`. */
export function needOf(application: Application): Need {
  switch (application.needs) {
    case "linkedin_paused":
      return {
        title: "LinkedIn is paused for seven days",
        what: "LinkedIn showed a security check or unusual activity. Nothing was sent. All your LinkedIn work waits for seven days, then you can review this application again.",
      };
    case "captcha":
      return {
        title: "A captcha needs you",
        what: "Solve the captcha in the live view. The assistant goes on by itself. If you do not come in time, the application waits here.",
      };
    case "email_code":
      return {
        title: "An e-mail code needs you",
        what: "The site sent you a code by e-mail. Type it in the live view. The assistant goes on by itself.",
      };
    case "forbidden":
      return {
        title: "The site blocked this page",
        what: "The site refused the page. Open it in the live view to see what it needs from you.",
      };
    case "required_left":
      return {
        title: "A question needs you",
        what: "Answer the questions marked for you below, then approve.",
      };
    case "payment_requested":
      return {
        title: "A payment was asked",
        what: `${PAYMENT_SENTENCE} No card detail was typed and nothing was sent. Reject it, or look at the posting yourself.`,
      };
    case "signin":
      return {
        title: `Needs you: sign in to ${siteOf(application)}`,
        what: "Open the live view to sign in or sign up yourself. The assistant then fills the form again and waits for your approval.",
      };
    default:
      return {
        title: "The form needs you",
        what: "The assistant could not go on with this form. Look at it below, then approve or reject it.",
      };
  }
}

/**
 * The run's wait for the person on the page (api run._wait_for_person), from the application's
 * events: when the last wait began and whether it ended; null when no wait was noted.
 */
export function waitOf(
  events: readonly ApplicationEvent[],
): { since: string; over: boolean } | null {
  let wait: { since: string; over: boolean } | null = null;
  for (const event of events) {
    if (event.kind === "waiting_person")
      wait = { since: event.at, over: false };
    else if (wait && event.kind === "person_not_back") wait.over = true;
  }
  return wait;
}

/** When a queued retry is due (api failures.after_failure's `due`), from its last event. */
export function retrySentence(events: readonly ApplicationEvent[]): string {
  const retry = events.filter((event) => event.kind === "retry").at(-1);
  const due = retry?.data.due;
  return typeof due === "string"
    ? `The last attempt did not finish. The assistant tries again at ${formatDateTime(due)}.`
    : "The last attempt did not finish. The assistant tries again by itself.";
}

const countSchema = z.object({ needs_you: z.number().int().default(0) });

/** Under the queue's prefix: whatever refreshes the queue refreshes the dot. */
export const needsCountKey = [...queueKey, "needs"] as const;

export const getNeedsCount = async (signal?: AbortSignal): Promise<number> => {
  const count = await request("/apply/queue/count", {
    signal,
    timeoutMs: 15_000,
    validate: (body) => countSchema.parse(body),
  });
  return count.needs_you;
};
