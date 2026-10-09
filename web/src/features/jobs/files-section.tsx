import { useQuery } from "@tanstack/react-query";
import {
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  File,
  FileCode,
  FileJson,
  FileText,
  LoaderCircle,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";

import { fetchFileJson, fileUrl, jobsKey, type JobFile } from "./api";
import { formatSize } from "./format";
import { prettyJson } from "./text";

const PDF_MAGIC = "%PDF-";

function FileIcon({ mime }: { mime: string }) {
  const props = {
    "aria-hidden": true,
    className: "size-4 shrink-0 text-muted-foreground",
  };
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
    <section aria-labelledby="files-title" className="flex flex-col gap-3">
      <h2 id="files-title" className="text-lg font-semibold tracking-tight">
        Files
      </h2>
      {files.length === 0 ? (
        <p className="text-sm text-muted-foreground">No files attached.</p>
      ) : (
        <ul
          aria-label="Files"
          className="flex flex-col divide-y rounded-lg border"
        >
          {files.map((file) => (
            <FileRow key={file.id} jobId={jobId} file={file} />
          ))}
        </ul>
      )}
    </section>
  );
}

function FileRow({ jobId, file }: { jobId: string; file: JobFile }) {
  const url = fileUrl(jobId, file.id);
  const pdf = file.mime === "application/pdf";
  const json = file.mime === "application/json";
  const [preview, setPreview] = useState(false);
  return (
    <li className="flex flex-col gap-2 p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <FileIcon mime={file.mime} />
        <span className="min-w-0 font-medium break-all">{file.name}</span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {formatSize(file.size)}
          {ORIGIN_LABELS[file.origin] ? `, ${ORIGIN_LABELS[file.origin]}` : ""}
        </span>
        <div className="ml-auto flex gap-2">
          {pdf ? (
            <Button
              variant="outline"
              size="sm"
              aria-expanded={preview}
              aria-label={`${preview ? "Hide" : "Show"} preview of ${file.name}`}
              onClick={() => {
                setPreview((open) => !open);
              }}
            >
              {preview ? (
                <EyeOff aria-hidden="true" />
              ) : (
                <Eye aria-hidden="true" />
              )}
              {preview ? "Hide preview" : "Preview"}
            </Button>
          ) : null}
          <Button asChild variant="outline" size="sm">
            <a
              href={url}
              target={pdf ? "_blank" : undefined}
              rel="noopener noreferrer"
              download={pdf ? undefined : file.name}
              aria-label={`${pdf ? "Open" : "Download"} ${file.name}`}
            >
              <Download aria-hidden="true" />
              {pdf ? "Open" : "Download"}
            </a>
          </Button>
        </div>
      </div>
      {pdf && preview ? <PdfPreview url={url} name={file.name} /> : null}
      {json ? <JsonViewer jobId={jobId} file={file} /> : null}
    </li>
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
        if (!response.ok)
          throw new Error(`The API answered ${response.status}.`);
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
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
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
  const content = useQuery({
    queryKey: [...jobsKey, "file", jobId, file.id],
    queryFn: ({ signal }) => fetchFileJson(jobId, file.id, signal),
    enabled: open,
    staleTime: Infinity,
  });
  return (
    <details
      className="group rounded-md border bg-muted/30"
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
      }}
    >
      <summary className="flex cursor-pointer items-center gap-1 px-3 py-2 text-sm select-none">
        <ChevronRight
          aria-hidden="true"
          className="size-4 transition-transform group-open:rotate-90"
        />
        Show {file.name}
      </summary>
      <div className="border-t px-3 py-2">
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
      </div>
    </details>
  );
}
