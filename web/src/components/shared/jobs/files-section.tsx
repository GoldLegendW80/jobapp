import {
  ChevronRight,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  File,
  FileCode,
  FileJson,
  FileText,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Section } from "@/components/blocks/section";
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Spinner } from "@/components/ui/spinner";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { describeError } from "@/lib/api";

import { fileUrl, type JobFile } from "@/features/jobs/api/jobs-api";
import { useJobFileJson } from "@/features/jobs/api/use-job";
import { formatSize } from "@/features/jobs/helpers/format";
import { prettyJson } from "@/features/jobs/helpers/text";

const PDF_MAGIC = "%PDF-";

function FileIcon({ mime }: { mime: string }) {
  const props = { "aria-hidden": true };
  if (mime === "application/pdf") return <FileText {...props} />;
  if (mime === "application/json") return <FileJson {...props} />;
  if (mime === "text/x-python") return <FileCode {...props} />;
  return <File {...props} />;
}

const ORIGIN_LABELS: Record<string, string> = {
  legacy: "imported",
  generated: "generated",
  upload: "uploaded",
};

/** Every file of the job: icon, name, size, a link to open or download it, and a preview. */
export function FilesSection({
  jobId,
  files,
}: {
  jobId: string;
  files: JobFile[];
}) {
  return (
    <Section title="Files">
      {files.length === 0 ? (
        <p className="text-sm text-muted-foreground">No files attached.</p>
      ) : (
        <ul aria-label="Files" className="flex flex-col gap-3">
          {files.map((file) => (
            <FileRow key={file.id} jobId={jobId} file={file} />
          ))}
        </ul>
      )}
    </Section>
  );
}

function FileRow({ jobId, file }: { jobId: string; file: JobFile }) {
  const url = fileUrl(jobId, file.id);
  const pdf = file.mime === "application/pdf";
  const json = file.mime === "application/json";
  const [preview, setPreview] = useState(false);
  const origin = ORIGIN_LABELS[file.origin];
  return (
    <Collapsible
      asChild
      open={preview}
      onOpenChange={setPreview}
      className="flex flex-col gap-2"
    >
      <li>
        <Attachment className="w-full">
          <AttachmentMedia>
            <FileIcon mime={file.mime} />
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle className="whitespace-normal break-all">
              {file.name}
            </AttachmentTitle>
            <AttachmentDescription className="tabular-nums">
              {formatSize(file.size)}
              {origin ? `, ${origin}` : ""}
            </AttachmentDescription>
          </AttachmentContent>
          <AttachmentActions className="gap-1">
            {pdf ? (
              <ActionTip tip={preview ? "Hide preview" : "Preview"}>
                <CollapsibleTrigger asChild>
                  <AttachmentAction
                    size="icon-sm"
                    aria-label={`${preview ? "Hide" : "Show"} preview of ${file.name}`}
                  >
                    {preview ? (
                      <EyeOff aria-hidden="true" />
                    ) : (
                      <Eye aria-hidden="true" />
                    )}
                  </AttachmentAction>
                </CollapsibleTrigger>
              </ActionTip>
            ) : null}
            <ActionTip tip={pdf ? "Open in a new tab" : "Download"}>
              <AttachmentAction asChild size="icon-sm">
                <a
                  href={url}
                  target={pdf ? "_blank" : undefined}
                  rel="noopener noreferrer"
                  download={pdf ? undefined : file.name}
                  aria-label={`${pdf ? "Open" : "Download"} ${file.name}`}
                >
                  {pdf ? (
                    <ExternalLink aria-hidden="true" />
                  ) : (
                    <Download aria-hidden="true" />
                  )}
                </a>
              </AttachmentAction>
            </ActionTip>
          </AttachmentActions>
        </Attachment>
        {/* Mounted only while shown: hiding it frees the PDF's blob. */}
        {pdf ? (
          <CollapsibleContent>
            <PdfPreview url={url} name={file.name} />
          </CollapsibleContent>
        ) : null}
        {json ? <JsonViewer jobId={jobId} file={file} /> : null}
      </li>
    </Collapsible>
  );
}

/** The word of an icon action, on hover and on focus. */
function ActionTip({ tip, children }: { tip: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent>{tip}</TooltipContent>
    </Tooltip>
  );
}

type PdfState =
  | { state: "loading" }
  | { state: "ready"; src: string }
  | { state: "error"; message: string };

/**
 * The PDF fetched with the session and shown from a blob: URL in an iframe. The api's own URL
 * cannot be framed (every answer carries frame-ancestors 'none' and X-Frame-Options DENY), and
 * Chrome refuses to show a PDF inside a sandboxed frame, so instead: the bytes must start with
 * %PDF-, the blob is typed application/pdf whatever the server said, and the browser's PDF viewer
 * renders it (a PDF never runs script in the app's origin). Any failure shows the open link.
 */
function PdfPreview({ url, name }: { url: string; name: string }) {
  const [pdf, setPdf] = useState<PdfState>({ state: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    const load = async () => {
      try {
        const response = await fetch(url, {
          credentials: "include",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("This file could not be loaded.");
        const bytes = await response.arrayBuffer();
        const head = new TextDecoder().decode(bytes.slice(0, PDF_MAGIC.length));
        if (head !== PDF_MAGIC) throw new Error("This file is not a PDF.");
        objectUrl = URL.createObjectURL(
          new Blob([bytes], { type: "application/pdf" }),
        );
        setPdf({ state: "ready", src: objectUrl });
      } catch (error) {
        if (controller.signal.aborted) return;
        setPdf({
          state: "error",
          message:
            error instanceof Error ? error.message : "The preview failed.",
        });
      }
    };
    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  if (pdf.state === "loading")
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Loading the preview
      </p>
    );
  if (pdf.state === "error")
    return (
      <p role="alert" className="text-sm text-muted-foreground">
        The preview could not be shown. {pdf.message}{" "}
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="underline"
        >
          Open {name}
        </a>
      </p>
    );
  return (
    <iframe
      src={pdf.src}
      title={`Preview of ${name}`}
      referrerPolicy="no-referrer"
      className="h-[70vh] min-h-96 w-full rounded-md border bg-white"
    />
  );
}

/** A JSON file, fetched when opened and shown pretty-printed as text. */
function JsonViewer({ jobId, file }: { jobId: string; file: JobFile }) {
  const [open, setOpen] = useState(false);
  const content = useJobFileJson(jobId, file.id, { enabled: open });
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          className="group h-auto w-fit justify-start gap-1 px-2 py-1 font-normal has-[>svg]:px-2"
        >
          <ChevronRight
            aria-hidden="true"
            className="transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none"
          />
          Show {file.name}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 rounded-md bg-muted px-3 py-2">
        {content.isPending ? (
          <p role="status" className="text-sm text-muted-foreground">
            Loading
          </p>
        ) : content.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {file.name} could not be loaded. {describeError(content.error)}
          </p>
        ) : (
          <pre
            aria-label={`Content of ${file.name}`}
            className="max-h-[60vh] overflow-auto text-xs leading-relaxed break-words whitespace-pre-wrap"
          >
            {prettyJson(content.data)}
          </pre>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
