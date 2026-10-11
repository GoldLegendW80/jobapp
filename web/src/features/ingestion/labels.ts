/** The words the documents list uses for a document's type, size, state and pages. */
import type { IngestionDocument } from "./documents-api";

export function kindLabel(document: IngestionDocument): string {
  switch (document.kind) {
    case "text":
      return document.source === "pasted" ? "Pasted text" : "Text";
    case "pdf":
      return "PDF";
    case "archive":
      return "Word or ZIP";
    case "image":
      return "Image";
    case "other":
      return "File";
  }
}

/** "812 bytes", "34 KB", "3.4 MB": short, for a list. */
export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} ${bytes === 1 ? "byte" : "bytes"}`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  const megabytes = bytes / (1024 * 1024);
  return `${megabytes >= 10 ? Math.round(megabytes) : megabytes.toFixed(1)} MB`;
}

export type StatusTone = "done" | "working" | "waiting" | "failed";

export interface StatusView {
  label: string;
  tone: StatusTone;
  /** A sentence under the row saying what happens next, when there is something to say. */
  detail?: string;
}

const NOT_READ_YET: Record<
  Exclude<IngestionDocument["kind"], "other">,
  string
> = {
  text: "text files",
  pdf: "PDF files",
  archive: "Word files and archives",
  image: "images",
};

/** How often the list is asked again while a parser is at work on one of its documents. */
export const WATCH_MS = 3000;

/**
 * True while a parser is at work on the document: it is pending and its kind has a reader. A
 * file kept for a reader that does not exist yet is pending too, but nothing will change until
 * a later version: it is not watched.
 */
export function isBeingRead(document: IngestionDocument): boolean {
  return document.status === "pending" && document.readable;
}

/** How often to ask the list again: every WATCH_MS while a document is being read, else never. */
export function watchInterval(
  documents: readonly IngestionDocument[] | undefined,
): number | false {
  return documents?.some(isBeingRead) ? WATCH_MS : false;
}

/**
 * What to tell the user about a document's state. A file kept for a reader that does not exist
 * yet in this version says so plainly, so nobody waits for something that will not move.
 */
export function statusView(document: IngestionDocument): StatusView {
  switch (document.status) {
    case "done":
      // Its text is there. An agent may read it, unless the user still has to look at it.
      return needsConfirmation(document)
        ? { label: "To check", tone: "waiting" }
        : { label: "Ready", tone: "done" };
    case "pending":
      if (isBeingRead(document))
        return { label: "Being read", tone: "working" };
      return {
        label: "Waiting",
        tone: "waiting",
        detail:
          document.kind === "other"
            ? // No reader is planned for a type the api does not know: no promise.
              "This version cannot read this type of file. Your file is kept."
            : `This version cannot read ${NOT_READ_YET[document.kind]} yet. Your file is kept, and it will be read as soon as it can.`,
      };
    case "awaiting_transcription":
      return {
        label: "Waiting for the agent",
        tone: "waiting",
        detail:
          "Its pages are images. The agent will read them at the next run.",
      };
    case "failed":
      return {
        label: "Could not be read",
        tone: "failed",
        detail: document.failed_reason ?? "This file could not be read.",
      };
  }
}

const plural = (count: number, word: string) =>
  `${count} ${count === 1 ? word : `${word}s`}`;

/** "12 pages read as text, 3 as images", when a parser counted the pages. */
export function pagesLabel(document: IngestionDocument): string | undefined {
  const { pages_text: asText, pages_image: asImages } = document;
  if (asText === null && asImages === null) return undefined;
  const parts: string[] = [];
  if (asText) parts.push(`${plural(asText, "page")} read as text`);
  if (asImages)
    parts.push(
      parts.length > 0
        ? `${asImages} as ${asImages === 1 ? "an image" : "images"}`
        : `${plural(asImages, "page")} kept as ${asImages === 1 ? "an image" : "images"}`,
    );
  return parts.length > 0 ? parts.join(", ") : undefined;
}

/** True while the user has not yet said that a flagged document is fine to read. */
export function needsConfirmation(document: IngestionDocument): boolean {
  return document.secret_warning && document.secret_confirmed_at === null;
}

/** True when the document has a text to show. */
export function hasText(document: IngestionDocument): boolean {
  return (
    document.status === "done" || document.status === "awaiting_transcription"
  );
}
