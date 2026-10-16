import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileUp, LoaderCircle, UserRoundPen } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type DragEvent,
  type RefObject,
} from "react";
import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  confirmDocument,
  documentsKey,
  failureSentence as uploadFailure,
  listDocuments,
  uploadDocument,
  type IngestionDocument,
} from "@/features/ingestion/documents-api";
import { StartDialog } from "@/features/ingestion/run-start";
import { cn } from "@/lib/utils";

import { ConsentDialog } from "./consent-dialog";
import {
  acceptConsent,
  baseCvsKey,
  consentKey,
  createBaseCv,
  failureSentence,
  getConsent,
  listBaseCvs,
  patchBaseCv,
  type BaseCv,
} from "./documents-api";
import { failedSentence, isOpen, pollInterval, WAIT_SENTENCES } from "./labels";

/** How often the uploaded file is read again while ingestion reads its text. */
export const READING_POLL_MS = 1500;
/** The review of what a profile run proposes. */
export const FILL_REVIEW = "/profile/fill?step=review";
const ACCEPTED = /\.(pdf|docx)$/i;
export const NOT_A_CV = "Choose a PDF or a Word document (DOCX).";

/**
 * Upload a CV and turn it into an editable base CV (task D24). Self contained: it reads and
 * writes through the api alone and assumes nothing of the page around it, so the CV tab of the
 * profile mounts it today and an onboarding step can mount it later. `openHref` says where a
 * converted CV opens.
 *
 * The file goes through ingestion's upload, which reads its text; the conversion starts once
 * the text is there, after the consent of what is sent (asked the first time; declined, a
 * button asks again). "Complete my
 * profile from this CV" opens ingestion's own run dialog on that file: it shows the estimate,
 * and nothing runs until the person clicks Start there; each proposed change is then reviewed on
 * the Fill my profile page.
 */
export function UploadPanel({
  openHref,
  className,
}: {
  /** The address of a base CV of the list, by its id. */
  openHref: (baseId: string) => string;
  className?: string;
}) {
  const client = useQueryClient();
  const titleId = useId();
  const hintId = useId();
  const input = useRef<HTMLInputElement>(null);
  const completer = useRef<HTMLButtonElement>(null);
  const [over, setOver] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [uploadId, setUploadId] = useState<string>();
  const [baseId, setBaseId] = useState<string>();
  const [declined, setDeclined] = useState(false);
  const [completing, setCompleting] = useState(false);
  /** The file whose conversion was asked for: once per upload. */
  const started = useRef<string>();

  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
  });
  const files = useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
    enabled: uploadId !== undefined && baseId === undefined,
    refetchInterval: (current) =>
      current.state.data?.items.find((one) => one.id === uploadId)?.status ===
      "pending"
        ? READING_POLL_MS
        : false,
  });
  const cvs = useQuery({
    queryKey: baseCvsKey,
    queryFn: ({ signal }) => listBaseCvs(signal),
    enabled: baseId !== undefined,
    refetchInterval: (current) => {
      const cv = current.state.data?.base_cvs.find((one) => one.id === baseId);
      return cv === undefined
        ? false
        : pollInterval(cv.render_state, cv.generation);
    },
  });

  const reset = () => {
    setProblem(undefined);
    setUploadId(undefined);
    setBaseId(undefined);
    setDeclined(false);
  };

  const send = useMutation({
    mutationFn: (file: File) => uploadDocument(file),
    onSuccess: (stored) => {
      setUploadId(stored.id);
      void client.invalidateQueries({ queryKey: documentsKey });
    },
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });
  const convert = useMutation({
    mutationFn: (id: string) =>
      createBaseCv({ from: "upload", ingestion_document_id: id }),
    onSuccess: (made) => {
      setBaseId(made.id);
      client.setQueryData<{ base_cvs: BaseCv[]; max: number }>(
        baseCvsKey,
        (old) =>
          old === undefined
            ? old
            : { ...old, base_cvs: [...old.base_cvs, made] },
      );
      void client.invalidateQueries({ queryKey: baseCvsKey });
    },
    onError: (error) => {
      setProblem(failureSentence(error));
    },
  });
  const agree = useMutation({
    mutationFn: acceptConsent,
    onSuccess: (accepted) => {
      client.setQueryData(consentKey, accepted);
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });
  const keep = useMutation({
    mutationFn: confirmDocument,
    onSuccess: () => client.invalidateQueries({ queryKey: documentsKey }),
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });
  const makeDefault = useMutation({
    mutationFn: (id: string) => patchBaseCv(id, { default: true }),
    onSuccess: () => client.invalidateQueries({ queryKey: baseCvsKey }),
    onError: (error) => {
      setProblem(failureSentence(error));
    },
  });

  const take = (file: File | undefined) => {
    if (file === undefined) return;
    reset();
    if (!ACCEPTED.test(file.name)) {
      setProblem(NOT_A_CV);
      return;
    }
    send.mutate(file);
  };
  const file = files.data?.items.find((one) => one.id === uploadId);
  const cv = cvs.data?.base_cvs.find((one) => one.id === baseId);
  const busy = send.isPending || convert.isPending;
  const read =
    file !== undefined &&
    file.status !== "pending" &&
    file.status !== "failed" &&
    !(file.secret_warning && file.secret_confirmed_at === null);
  const accepted = consent.data?.accepted;
  const convertNow = convert.mutate;
  // The text is read and what is sent agreed to: convert it, once.
  useEffect(() => {
    if (!read || uploadId === undefined || accepted !== true) return;
    if (started.current === uploadId) return;
    started.current = uploadId;
    convertNow(uploadId);
  }, [read, uploadId, accepted, convertNow]);
  const asking =
    read && accepted === false && !declined && baseId === undefined;

  return (
    <section
      aria-labelledby={titleId}
      className={cn("flex flex-col gap-3 rounded-lg border p-4", className)}
      data-testid="upload-panel"
    >
      <h3 id={titleId} className="font-semibold">
        Upload your CV
      </h3>
      <div
        data-over={over}
        data-testid="upload-drop"
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setOver(false);
        }}
        onDrop={(event: DragEvent<HTMLDivElement>) => {
          event.preventDefault();
          setOver(false);
          take(event.dataTransfer.files[0]);
        }}
        className={cn(
          "flex flex-col items-start gap-3 rounded-md border border-dashed p-4 transition-colors",
          over && "border-primary bg-accent",
        )}
      >
        <p id={hintId} className="text-sm text-muted-foreground">
          Drop a PDF or a Word document here, or choose it. Its text is sent to
          the model of your credential and comes back as a CV you can edit; the
          file stays in your documents.
        </p>
        <input
          ref={input}
          type="file"
          accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          hidden
          data-testid="upload-cv-file"
          onChange={(event) => {
            take(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          aria-describedby={hintId}
          onClick={() => input.current?.click()}
        >
          <FileUp aria-hidden="true" />
          Choose a file
        </Button>
      </div>

      {problem !== undefined ? (
        <Alert variant="destructive">
          <AlertTitle>Your CV could not be converted</AlertTitle>
          <AlertDescription>{problem}</AlertDescription>
        </Alert>
      ) : null}

      {problem === undefined &&
      baseId === undefined &&
      uploadId !== undefined ? (
        <Reading
          file={file}
          busy={busy || keep.isPending}
          declined={declined}
          onConvert={() => {
            setDeclined(false);
          }}
          onKeep={(id) => {
            keep.mutate(id);
          }}
        />
      ) : null}
      {send.isPending ? <Status>Sending your file</Status> : null}
      {consent.isError && uploadId !== undefined ? (
        <p className="text-sm text-destructive">
          What is sent could not be loaded. Reload the page to try again.
        </p>
      ) : null}

      {cv !== undefined ? (
        <Result
          cv={cv}
          openHref={openHref}
          defaulting={makeDefault.isPending}
          completer={completer}
          onDefault={() => {
            makeDefault.mutate(cv.id);
          }}
          onComplete={() => {
            setCompleting(true);
          }}
        />
      ) : null}

      {asking && consent.data !== undefined && uploadId !== undefined ? (
        <ConsentDialog
          consent={consent.data}
          open
          busy={agree.isPending || convert.isPending}
          error={agree.isError ? failureSentence(agree.error) : undefined}
          onClose={() => {
            agree.reset();
            setDeclined(true);
          }}
          onAgree={(version) => {
            agree.mutate(version);
          }}
        />
      ) : null}
      {completing && uploadId !== undefined ? (
        <StartDialog
          documentIds={[uploadId]}
          opener={completer}
          onClose={() => {
            setCompleting(false);
          }}
        />
      ) : null}
    </section>
  );
}

function Status({ children }: { children: string }) {
  return (
    <p
      role="status"
      className="flex items-center gap-2 text-sm text-muted-foreground"
    >
      <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
      {children}
    </p>
  );
}

/** The uploaded file while ingestion reads it, then the click that converts it. */
function Reading({
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
    return <Status>Reading your CV</Status>;
  if (file.status === "failed")
    return (
      <Alert variant="destructive">
        <AlertTitle>Your CV could not be read</AlertTitle>
        <AlertDescription>
          {file.failed_reason ?? "Upload it again, or another file."}
        </AlertDescription>
      </Alert>
    );
  if (file.secret_warning && file.secret_confirmed_at === null)
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
  if (!declined) return <Status>Reading your CV</Status>;
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

/** The converted CV: its state, then where it opens, the default, and the profile completion. */
function Result({
  cv,
  openHref,
  defaulting,
  completer,
  onDefault,
  onComplete,
}: {
  cv: BaseCv;
  openHref: (baseId: string) => string;
  defaulting: boolean;
  completer: RefObject<HTMLButtonElement>;
  onDefault: () => void;
  onComplete: () => void;
}) {
  const generation = cv.generation;
  if (isOpen(generation))
    return generation?.status === "waiting" && generation.wait_reason ? (
      <p className="text-sm" data-testid="upload-waiting">
        {WAIT_SENTENCES[generation.wait_reason]}
      </p>
    ) : (
      <Status>Reading your CV</Status>
    );
  if (generation?.status === "failed" || generation?.status === "cancelled")
    return (
      <Alert variant="destructive">
        <AlertTitle>Your CV could not be converted</AlertTitle>
        <AlertDescription>{failedSentence(generation.error)}</AlertDescription>
      </Alert>
    );
  return (
    <div className="flex flex-col gap-3" data-testid="upload-result">
      <p className="text-sm">
        <bdi className="font-medium">{cv.name}</bdi> is ready to edit
        {cv.is_default ? ". It is your default CV." : "."}{" "}
        <Link
          to={openHref(cv.id)}
          className="font-medium underline underline-offset-4"
        >
          Open it
        </Link>
      </p>
      <div className="flex flex-wrap gap-2">
        {cv.is_default ? null : (
          <Button variant="outline" disabled={defaulting} onClick={onDefault}>
            Make this my default CV
          </Button>
        )}
        <Button ref={completer} variant="outline" onClick={onComplete}>
          <UserRoundPen aria-hidden="true" />
          Complete my profile from this CV
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Nothing changes in your profile until you accept each proposed change on
        the{" "}
        <Link to={FILL_REVIEW} className="underline underline-offset-4">
          Fill my profile page
        </Link>
        .
      </p>
    </div>
  );
}
