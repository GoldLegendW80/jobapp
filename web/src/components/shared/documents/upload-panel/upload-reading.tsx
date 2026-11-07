import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { IngestionDocument } from "@/features/ingestion/api/documents-api";

import { needsKeeping } from "@/features/documents/helpers/upload-cv";
import { UploadStatus } from "./upload-status";

/** The uploaded file while ingestion reads it, then the click that converts it. */
export function UploadReading({
  file,
  busy,
  declined,
  onConvert,
  onKeep,
}: {
  file: IngestionDocument | undefined;
  busy: boolean;
  declined: boolean;
  onConvert: () => void;
  onKeep: (id: string) => void;
}) {
  if (file === undefined || file.status === "pending")
    return <UploadStatus>Reading your CV</UploadStatus>;
  if (file.status === "failed")
    return (
      <Alert variant="destructive">
        <AlertTitle>Your CV could not be read</AlertTitle>
        <AlertDescription>
          {file.failed_reason ?? "Upload it again, or another file."}
        </AlertDescription>
      </Alert>
    );
  if (needsKeeping(file))
    return (
      <Alert>
        <AlertTitle>This file may hold a password or a key</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          Keep it only if it is your CV and nothing secret is in it.
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => {
              onKeep(file.id);
            }}
          >
            Keep it
          </Button>
        </AlertDescription>
      </Alert>
    );
  if (!declined) return <UploadStatus>Reading your CV</UploadStatus>;
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="text-sm" data-testid="upload-declined">
        Confirm what is sent to convert <bdi>{file.name}</bdi>.
      </p>
      <Button disabled={busy} onClick={onConvert}>
        Convert it into my CV
      </Button>
    </div>
  );
}
