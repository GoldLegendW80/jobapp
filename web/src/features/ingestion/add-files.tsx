import { Upload, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { megabytes, type Limits } from "./documents-api";
import { sizeLabel } from "./labels";
import type { UploadItem, Uploads } from "./use-uploads";

const FOLDER = "A folder cannot be added. Open it and add the files inside.";

/** What was dropped: the files, and the names of the folders among them. */
function dropped(transfer: DataTransfer): { files: File[]; folders: string[] } {
  const files: File[] = [];
  const folders: string[] = [];
  // DataTransferItemList is array like in browsers; a drop without items falls back to files.
  const items = Array.from(
    (transfer.items as ArrayLike<DataTransferItem> | undefined) ?? [],
  );
  if (items.length === 0) return { files: Array.from(transfer.files), folders };
  for (const item of items) {
    if (item.kind !== "file") continue;
    const entry =
      typeof item.webkitGetAsEntry === "function"
        ? item.webkitGetAsEntry()
        : null;
    if (entry?.isDirectory) {
      folders.push(entry.name);
      continue;
    }
    const file = item.getAsFile();
    if (file) files.push(file);
  }
  return { files, folders };
}

/**
 * Drop files or choose them. Each file is sent on its own and shows its progress; a file the
 * server refuses stays in the list with the reason until it is dismissed. Works without a
 * mouse: "Choose files" opens the system's picker.
 */
export function AddFiles({
  limits,
  uploads,
}: {
  limits: Limits;
  uploads: Uploads;
}) {
  const input = useRef<HTMLInputElement>(null);
  const choose = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [over, setOver] = useState(false);
  const titleId = useId();
  const hintId = useId();

  // Cancel and Dismiss remove the row that holds the keyboard focus. Once the row is gone the
  // focus goes to the row that took its place, or to "Choose files" when none is left.
  const leaving = useRef<{ id: number; next: number | undefined }>();
  const { items } = uploads;
  useEffect(() => {
    const gone = leaving.current;
    if (!gone || items.some((item) => item.id === gone.id)) return;
    leaving.current = undefined;
    // Only when the focus went with the row: the user may have moved on in the meantime.
    const active = document.activeElement;
    if (active && active !== document.body && active.isConnected) return;
    const buttons = (selector: string) =>
      list.current?.querySelector<HTMLElement>(selector) ?? null;
    const target =
      (gone.next === undefined
        ? null
        : buttons(`[data-upload="${gone.next}"] button`)) ??
      buttons("button") ??
      choose.current;
    target?.focus();
  }, [items]);
  const remove = (id: number) => {
    const index = items.findIndex((item) => item.id === id);
    leaving.current = { id, next: (items[index + 1] ?? items[index - 1])?.id };
    uploads.remove(id);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setOver(false);
    const { files, folders } = dropped(event.dataTransfer);
    for (const name of folders) uploads.refuse(name, FOLDER);
    uploads.add(files, limits.file_bytes);
  };

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3">
      <div
        data-over={over}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setOver(false);
        }}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border border-dashed px-4 py-8 text-center transition-colors",
          over && "border-primary bg-accent",
        )}
      >
        <Upload aria-hidden="true" className="size-6 text-muted-foreground" />
        <h3 id={titleId} className="text-sm font-medium">
          Drop files here, or choose them
        </h3>
        <p id={hintId} className="max-w-prose text-sm text-muted-foreground">
          A CV, a cover letter, notes on your projects, your LinkedIn export
          (the ZIP). PDF, Word, text, Markdown, JSON, CSV, PNG or JPEG, up to{" "}
          {megabytes(limits.file_bytes)} MB each. From a LinkedIn export, your
          own profile files are the only ones read: connections, messages,
          contacts and invitations are ignored.
        </p>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          data-testid="document-files"
          onChange={(event) => {
            uploads.add(
              Array.from(event.target.files ?? []),
              limits.file_bytes,
            );
            // The same file can be chosen again after a refusal.
            event.target.value = "";
          }}
        />
        <Button
          ref={choose}
          type="button"
          variant="outline"
          aria-describedby={hintId}
          onClick={() => input.current?.click()}
        >
          Choose files
        </Button>
      </div>
      {items.length > 0 ? (
        <ul
          ref={list}
          aria-label="Files being added"
          className="flex flex-col gap-2"
        >
          {items.map((item) => (
            <UploadRow
              key={item.id}
              item={item}
              onRemove={() => {
                remove(item.id);
              }}
            />
          ))}
        </ul>
      ) : null}
      <p role="status" aria-live="polite" className="sr-only">
        {uploads.announcement}
      </p>
    </section>
  );
}

function UploadRow({
  item,
  onRemove,
}: {
  item: UploadItem;
  onRemove: () => void;
}) {
  const moving = item.state === "queued" || item.state === "uploading";
  const percent = Math.round(item.progress * 100);
  const state =
    item.state === "queued"
      ? "Waiting"
      : item.state === "uploading"
        ? percent >= 100
          ? "Finishing"
          : `${percent}%`
        : item.state === "duplicate"
          ? "Already there"
          : "Not added";
  return (
    <li
      data-upload={item.id}
      data-state={item.state}
      className="flex flex-col gap-1.5 rounded-md border px-3 py-2 text-sm"
    >
      <div className="flex items-center gap-2">
        <bdi className="min-w-0 flex-1 truncate font-medium">{item.name}</bdi>
        {moving ? (
          <span className="shrink-0 text-xs text-muted-foreground">
            {sizeLabel(item.size)}
          </span>
        ) : null}
        <span
          className={cn(
            "shrink-0 text-xs tabular-nums text-muted-foreground",
            item.state === "failed" && "font-medium text-destructive",
          )}
        >
          {state}
        </span>
        <Button
          type="button"
          size="icon-xs"
          variant="ghost"
          aria-label={`${moving ? "Cancel" : "Dismiss"} ${item.name}`}
          onClick={onRemove}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
      {moving ? (
        <div
          role="progressbar"
          aria-label={`Sending ${item.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        >
          <div
            className="h-full rounded-full bg-primary transition-[width]"
            style={{ width: `${percent}%` }}
          />
        </div>
      ) : item.state === "failed" ? (
        // No role="alert": the live region below already says it, once.
        <p className="text-destructive">{item.message}</p>
      ) : (
        <p className="text-muted-foreground">{item.message}</p>
      )}
    </li>
  );
}
