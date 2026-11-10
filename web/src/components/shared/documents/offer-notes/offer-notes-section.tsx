/**
 * "Offer" on a posting's page (SPEC-documents.md, "Reading an offer"): from the interview on,
 * "Read an offer" takes the letter the person received (a PDF or a Word file, through
 * ingestion's upload), with no consent asked, and the notes the assistant writes on it show
 * here, with their chat. Nothing is read without that click.
 */
import { FileUp } from "lucide-react";
import { useRef } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import {
  useOfferNotes,
  useReadOffer,
} from "@/features/documents/api/use-offer-notes";
import {
  INTRO,
  OFFER_FILE_TYPES,
  readingState,
  isOfferStage,
  stoppedSentence,
  truncatedSentence,
} from "@/features/documents/helpers/offer-notes";
import { needsKeeping } from "@/features/documents/helpers/upload-cv";
import { OfferNotesChat } from "./offer-notes-chat";
import { OfferNotesView } from "./offer-notes-view";

export function OfferNotesSection({ job }: { job: JobDetail }) {
  if (!isOfferStage(job.status)) return null;
  return <OfferSection job={job} />;
}

function OfferSection({ job }: { job: JobDetail }) {
  const notes = useOfferNotes(job.id);
  const input = useRef<HTMLInputElement>(null);
  const reader = useReadOffer(job.id);
  const shown = notes.data;
  const state = readingState(shown);
  const written = shown !== undefined && shown.version !== null;
  const actions = (
    <>
      <Input
        ref={input}
        type="file"
        accept={OFFER_FILE_TYPES}
        hidden
        data-testid="offer-letter-file"
        onChange={(event) => {
          reader.take(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <Button
        variant={written ? "outline" : "default"}
        size="sm"
        disabled={reader.pending || state === "reading" || shown?.busy === true}
        onClick={() => input.current?.click()}
      >
        <FileUp aria-hidden="true" />
        {written ? "Read another letter" : "Read an offer"}
      </Button>
      {written && shown.document_id !== null ? (
        <OfferNotesChat
          jobId={job.id}
          documentId={shown.document_id}
          reading={state === "reading"}
        />
      ) : null}
    </>
  );
  return (
    <Section
      title="Offer"
      description={written ? undefined : INTRO}
      actions={actions}
    >
      <Card>
        <CardContent className="flex flex-col gap-4">
          <Steps reader={reader} />
          {notes.isError ? (
            <LoadError query={notes}>The notes could not be loaded.</LoadError>
          ) : state === "reading" ? (
            <p role="status" className="flex items-center gap-2 text-sm">
              <Spinner aria-hidden="true" role={undefined} />
              Reading the letter. This takes a minute or two.
            </p>
          ) : state === "failed" ? (
            <Alert variant="destructive">
              <AlertDescription className="flex flex-col items-start gap-2">
                {stoppedSentence(shown?.reading?.error ?? null)}
                {shown?.letter ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={reader.pending}
                    onClick={() => {
                      if (shown.letter) reader.again(shown.letter.id);
                    }}
                  >
                    Read <bdi>{shown.letter.name}</bdi> again
                  </Button>
                ) : null}
              </AlertDescription>
            </Alert>
          ) : null}
          {written && shown.truncated && shown.read_chars !== null ? (
            <Alert data-testid="offer-letter-truncated">
              <AlertDescription>
                {truncatedSentence(shown.read_chars)}
              </AlertDescription>
            </Alert>
          ) : null}
          {written && shown.notes !== null ? (
            <OfferNotesView
              notes={shown.notes}
              language={shown.language}
              pay={shown.pay}
            />
          ) : state === "none" && notes.isSuccess && !reader.pending ? (
            <p className="text-sm text-muted-foreground">No letter read yet.</p>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}

/** Where the chosen letter stands before its reading starts. */
function Steps({ reader }: { reader: ReturnType<typeof useReadOffer> }) {
  const { file } = reader;
  if (reader.problem !== undefined)
    return (
      <Alert variant="destructive">
        <AlertDescription>{reader.problem}</AlertDescription>
      </Alert>
    );
  if (!reader.pending) return null;
  if (file?.status === "failed")
    return (
      <Alert variant="destructive">
        <AlertTitle>The letter could not be read</AlertTitle>
        <AlertDescription>
          {file.failed_reason ?? "Upload it again, or another file."}
        </AlertDescription>
      </Alert>
    );
  if (file !== undefined && needsKeeping(file))
    return (
      <Alert>
        <AlertTitle>This file may hold a password or a key</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          Keep it only if it is your offer letter and nothing secret is in it.
          <Button
            size="sm"
            variant="outline"
            disabled={reader.keep.isPending}
            onClick={() => {
              reader.keep.mutate(file.id);
            }}
          >
            Keep it
          </Button>
        </AlertDescription>
      </Alert>
    );
  return (
    <p role="status" className="flex items-center gap-2 text-sm">
      <Spinner aria-hidden="true" role={undefined} />
      Sending the letter
    </p>
  );
}
