import { RotateCw } from "lucide-react";
import { useParams } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useProfile } from "@/features/profile/api/use-profile";
import { describeError } from "@/lib/api";

import { failureSentence } from "@/features/documents/api/documents-api";
import { useBaseCvs } from "@/features/documents/api/use-base-cvs";
import { useNewBaseCv } from "@/features/documents/api/use-new-base-cv";
import { CV_LIST_PATH } from "@/features/documents/helpers/base-cvs";
import { ConsentDialog } from "@/components/shared/documents/consent-dialog";
import { CvOverview } from "./cv-tab/cv-overview";
import { NewCvDialog } from "./cv-tab/new-cv-dialog";
import { CvNotFound, OpenedCv } from "./cv-tab/opened-cv";
import { UploadPanel } from "@/components/shared/documents/upload-panel";

/**
 * The CV tab of the profile: the person's base CVs, one of them the default, and the document
 * view of the one opened (/profile/cv/:id). Building from the profile sends the profile to a
 * model, so it asks for the consent first; a copy sends nothing.
 */
export function CvTab() {
  const { id } = useParams();
  const list = useBaseCvs();
  const build = useNewBaseCv();
  const { consent, create, agree } = build;
  // The same read as the profile page's own, so it is already in the cache.
  const profile = useProfile();

  if (list.isPending) {
    return (
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Loading your CVs
      </div>
    );
  }
  if (list.isError) {
    return (
      <div className="flex flex-col gap-3">
        <Alert variant="destructive">
          <AlertTitle>Your CVs could not be loaded</AlertTitle>
          <AlertDescription>{describeError(list.error)}</AlertDescription>
        </Alert>
        <div>
          <Button
            variant="outline"
            disabled={list.isFetching}
            onClick={() => void list.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  const { base_cvs: cvs, max } = list.data;
  const opened =
    id === undefined ? undefined : cvs.find((one) => one.id === id);

  return (
    <div className="flex flex-col gap-4">
      {id !== undefined ? (
        opened === undefined ? (
          <CvNotFound />
        ) : (
          <OpenedCv cv={opened} cvs={cvs} />
        )
      ) : (
        <CvOverview
          cvs={cvs}
          max={max}
          building={create.isPending}
          consentMissing={consent.data?.accepted === false}
          minFit={consent.data?.min_fit}
          consentReady={consent.data !== undefined}
          consentError={consent.isError}
          noExperience={
            profile.data !== undefined &&
            profile.data.sections.experience.employers.length === 0
          }
          onNew={() => {
            build.setCreating(true);
          }}
          onBuild={() => {
            build.start({ from: "profile" });
          }}
        />
      )}

      {id === undefined ? (
        <UploadPanel openHref={(to) => `${CV_LIST_PATH}/${to}`} />
      ) : null}
      <NewCvDialog
        key={build.creating ? "open" : "closed"}
        open={build.creating}
        cvs={cvs}
        busy={create.isPending}
        onClose={() => {
          build.setCreating(false);
        }}
        onCreate={build.start}
      />
      {consent.data !== undefined && build.asking !== undefined ? (
        <ConsentDialog
          consent={consent.data}
          open
          busy={agree.isPending || create.isPending}
          error={agree.isError ? failureSentence(agree.error) : undefined}
          onClose={build.closeConsent}
          onAgree={build.agreeAndBuild}
        />
      ) : null}
    </div>
  );
}
