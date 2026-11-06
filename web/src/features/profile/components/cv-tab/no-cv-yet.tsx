import { FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";

/** The CV tab before the first CV: what a CV is for, and the build from the profile. */
export function NoCvYet({
  building,
  consentMissing,
  consentReady,
  consentError,
  noExperience,
  onBuild,
}: {
  building: boolean;
  consentMissing: boolean;
  consentReady: boolean;
  consentError: boolean;
  noExperience: boolean;
  onBuild: () => void;
}) {
  return (
    <Empty
      role="region"
      aria-labelledby="empty-heading"
      className="rounded-xl bg-card md:p-8"
    >
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <FileText aria-hidden="true" />
        </EmptyMedia>
        <EmptyTitle className="text-base font-semibold">
          <h3 id="empty-heading">You have no CV yet</h3>
        </EmptyTitle>
        <EmptyDescription>
          The app can write one from your profile. It becomes the default and
          every document for a posting starts from it.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        {noExperience ? (
          <p className="text-sm font-medium" data-testid="no-experience-line">
            Add your CV or fill your experience to get documents for each
            posting.
          </p>
        ) : null}
        {consentMissing ? (
          <p className="text-sm font-medium" data-testid="confirm-line">
            Confirm what is sent to build your CV.
          </p>
        ) : null}
        {consentError ? (
          <p className="text-sm text-destructive">
            What is sent could not be loaded. Reload the page to try again.
          </p>
        ) : null}
        <Button disabled={building || !consentReady} onClick={onBuild}>
          {building ? <Spinner aria-hidden="true" role={undefined} /> : null}
          Build it from my profile
        </Button>
      </EmptyContent>
    </Empty>
  );
}
