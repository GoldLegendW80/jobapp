import { useId } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardTitle } from "@/components/ui/card";
import { StartPanel } from "@/components/shared/ingestion/run-start";
import { cn } from "@/lib/utils";

import { failureSentence } from "@/features/documents/api/documents-api";
import { useCvUpload } from "@/features/documents/api/use-cv-upload";
import { READING_FOR_PROFILE } from "@/features/documents/helpers/upload-cv";
import { ConsentDialog } from "./consent-dialog";
import { UploadDropZone } from "./upload-panel/upload-drop-zone";
import { UploadReading } from "./upload-panel/upload-reading";
import { UploadResult } from "./upload-panel/upload-result";
import { UploadStatus } from "./upload-panel/upload-status";

/**
 * Upload a CV and turn it into an editable base CV (task D24). Self contained: it reads and
 * writes through the api alone and assumes nothing of the page around it, so the CV tab of the
 * profile and an onboarding step mount it. `openHref` says where a converted CV opens.
 *
 * The file goes through ingestion's upload, which reads its text; the conversion starts once
 * the text is there, after the consent of what is sent (asked the first time; declined, a
 * button asks again). The same dialog asks the consent of the Fill my profile run when it is
 * missing (owner, 2026-11-05: one dialog, both texts, each recorded with its own version).
 * "Complete my profile from this CV" is ingestion's start button on that file, with the token
 * range of its estimate; nothing runs until the person clicks it. While the run reads, the
 * panel says so, and when it ends the review opens by itself on the Fill my profile page.
 */
export function UploadPanel({
  openHref,
  titled = true,
  className,
}: {
  /** The address of a base CV of the list, by its id. */
  openHref: (baseId: string) => string;
  /** false: the section around the panel already says "Upload your CV". */
  titled?: boolean;
  className?: string;
}) {
  const titleId = useId();
  const upload = useCvUpload();
  const { consent, runConsent, profileRun, uploadId, cv, agree } = upload;

  return (
    <Card
      role="region"
      aria-labelledby={titled ? titleId : undefined}
      aria-label={titled ? undefined : "Upload your CV"}
      className={cn("gap-3 p-4 sm:p-5", className)}
      data-testid="upload-panel"
    >
      {titled ? (
        <CardTitle>
          <h3 id={titleId}>Upload your CV</h3>
        </CardTitle>
      ) : null}
      <UploadDropZone busy={upload.busy} onFile={upload.take} />

      {upload.problem !== undefined ? (
        <Alert variant="destructive">
          <AlertTitle>Your CV could not be converted</AlertTitle>
          <AlertDescription>{upload.problem}</AlertDescription>
        </Alert>
      ) : null}

      {upload.problem === undefined &&
      upload.baseId === undefined &&
      uploadId !== undefined ? (
        <UploadReading
          file={upload.file}
          busy={upload.busy || upload.keep.isPending}
          declined={upload.declined}
          onConvert={upload.askAgain}
          onKeep={(id) => {
            upload.keep.mutate(id);
          }}
        />
      ) : null}
      {upload.send.isPending ? (
        <UploadStatus>Sending your file</UploadStatus>
      ) : null}
      {consent.isError && uploadId !== undefined ? (
        <p className="text-sm text-destructive">
          What is sent could not be loaded. Reload the page to try again.
        </p>
      ) : null}

      {cv !== undefined ? (
        <UploadResult
          cv={cv}
          openHref={openHref}
          defaulting={upload.makeDefault.isPending}
          onDefault={() => {
            upload.makeDefault.mutate(cv.id);
          }}
          complete={
            uploadId === undefined ? null : profileRun.following !==
              undefined ? (
              <UploadStatus>{READING_FOR_PROFILE}</UploadStatus>
            ) : (
              <StartPanel
                documentIds={[uploadId]}
                label="Complete my profile from this CV"
                onStarted={(started) => {
                  profileRun.follow(started.id);
                }}
              />
            )
          }
        />
      ) : null}

      {upload.asking && consent.data !== undefined && uploadId !== undefined ? (
        <ConsentDialog
          consent={consent.data}
          open
          busy={agree.isPending || upload.convert.isPending}
          error={agree.isError ? failureSentence(agree.error) : undefined}
          onClose={() => {
            agree.reset();
            upload.decline();
          }}
          onAgree={(version, runVersion) => {
            agree.mutate({ version, runVersion });
          }}
          {...(runConsent.data && !runConsent.data.accepted
            ? { run: runConsent.data }
            : {})}
        />
      ) : null}
    </Card>
  );
}
