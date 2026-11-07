import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import { LINKEDIN_OFF } from "@/features/apply/helpers/apply-offer";

type StartMode = "apply" | "manual";

/**
 * Apply and Apply myself, for a posting with no application yet. On LinkedIn before the person
 * allowed it, only the sentence that says how and the posting's own link are offered.
 */
export function StartButtons({
  busy,
  settingsRead,
  starting,
  linkedinBlocked,
  linkedinPosting,
  onStart,
}: {
  busy: boolean;
  /** What a click does is the person's setting: never before it is read. */
  settingsRead: boolean;
  /** The start on its way, for its spinner. */
  starting: StartMode | null;
  linkedinBlocked: boolean;
  linkedinPosting: string | null;
  onStart: (mode: StartMode) => void;
}) {
  if (linkedinBlocked)
    return (
      <>
        <p className="text-sm text-muted-foreground">{LINKEDIN_OFF}</p>
        {linkedinPosting ? (
          <Button variant="outline" className="w-full @sm:w-fit" asChild>
            <a href={linkedinPosting} target="_blank" rel="noopener noreferrer">
              Open LinkedIn and apply myself
            </a>
          </Button>
        ) : null}
      </>
    );
  return (
    <>
      <Button
        className="w-full @sm:w-fit"
        disabled={busy || !settingsRead}
        onClick={() => {
          onStart("apply");
        }}
      >
        {starting === "apply" ? (
          <Spinner aria-hidden="true" role={undefined} />
        ) : null}
        Apply
      </Button>
      <Button
        variant="outline"
        className="w-full @sm:w-fit"
        disabled={busy}
        onClick={() => {
          onStart("manual");
        }}
      >
        {starting === "manual" ? (
          <Spinner aria-hidden="true" role={undefined} />
        ) : null}
        Apply myself
      </Button>
    </>
  );
}
