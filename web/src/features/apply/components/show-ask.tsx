import { Link } from "react-router";
import { toast, type ExternalToast } from "sonner";

import type { AskToast } from "../api/use-ask-toasts";

// Long enough to read the posting and reach the link; the card keeps the ask after it.
export const ASK_TOAST_MS = 30_000;

const LINK =
  "rounded-sm font-medium text-foreground underline underline-offset-4 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** The toast of one ask of the apply agent (A25): the posting, and a link to its application. */
export function showAsk(
  ask: AskToast,
  options: Pick<ExternalToast, "toasterId" | "duration"> = {},
) {
  const id = `ask:${ask.key}`;
  toast.warning("The assistant needs you", {
    id,
    duration: ASK_TOAST_MS,
    ...options,
    description: (
      <span className="flex flex-col gap-1">
        <span className="break-words">
          {`${ask.title} at ${ask.company}: it waits for your answer or for you in the browser.`}
        </span>
        <Link
          to={`/jobs/${encodeURIComponent(ask.postingId)}`}
          className={LINK}
          onClick={() => {
            toast.dismiss(id);
          }}
        >
          Open the application
        </Link>
      </span>
    ),
  });
}
