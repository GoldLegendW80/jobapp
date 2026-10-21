import { useCallback, useEffect, useRef, useState } from "react";

import { isApiError } from "@/lib/api";

import {
  failureSentence,
  megabytes,
  uploadDocument,
  type StoredDocument,
} from "./documents-api";

export interface UploadItem {
  id: number;
  name: string;
  size: number;
  /**
   * queued: waits its turn. uploading: on its way. duplicate: the same file was already there.
   * failed: refused, with the reason in `message`. A file that was added simply leaves the list.
   */
  state: "queued" | "uploading" | "duplicate" | "failed";
  /** Part of the body sent so far, 0 to 1. */
  progress: number;
  message?: string;
}

export interface UploadsState {
  items: UploadItem[];
  /** One sentence about the last thing that happened, for a polite live region. */
  announcement: string;
}

export interface Uploads extends UploadsState {
  /** Queue files. `fileBytes` is the size limit; a file over it, or empty, is refused here. */
  add: (files: File[], fileBytes: number) => void;
  /** Say that something dropped was not a file (a folder). */
  refuse: (name: string, message: string) => void;
  /** Stop an upload that is queued or on its way, or dismiss a notice. */
  remove: (id: number) => void;
}

/** What the page is told about: every file kept, and every upload stopped on its way. */
export interface UploadEvents {
  /** The server kept the file, or already had it. */
  onStored: (stored: StoredDocument) => void;
  /**
   * An upload on its way was stopped. Its bytes may all have arrived before the stop did, in
   * which case the server keeps the file: the page looks at the list again.
   */
  onCancelled: () => void;
}

const SECRET_HINT =
  " It looks like it holds a password or a key: check it in the list below.";

/** What to say once a file is in: added, added with a warning, already there, or not replaced. */
export function storedSentence(name: string, stored: StoredDocument): string {
  if (stored.refused) return `${name}: ${stored.refused}`;
  if (stored.duplicate) {
    return stored.name === name
      ? `${name} is already in your documents.`
      : `${name} is already in your documents, as ${stored.name}.`;
  }
  const flagged = stored.secret_warning && stored.secret_confirmed_at === null;
  return `${name} added.${flagged ? SECRET_HINT : ""}`;
}

/**
 * The files being added, outside React: sent one at a time, each with its progress, so two
 * large uploads never compete on a slow link and the server never holds more than it must.
 * `onChange` receives every new state, `events` what the page has to know (UploadEvents).
 */
export class UploadQueue {
  #items: UploadItem[] = [];
  #announcement = "";
  #waiting: Array<{ id: number; file: File }> = [];
  #active: { id: number; controller: AbortController } | undefined;
  #nextId = 1;
  #stopped = false;
  readonly #onChange: (state: UploadsState) => void;
  readonly #events: UploadEvents;

  constructor(onChange: (state: UploadsState) => void, events: UploadEvents) {
    this.#onChange = onChange;
    this.#events = events;
  }

  add(files: File[], fileBytes: number): void {
    for (const file of files) {
      if (file.size === 0) {
        this.refuse(file.name, "The file is empty.");
      } else if (file.size > fileBytes) {
        this.refuse(
          file.name,
          `The file is larger than ${megabytes(fileBytes)} MB.`,
        );
      } else {
        const id = this.#nextId++;
        this.#waiting.push({ id, file });
        this.#items = [
          ...this.#items,
          {
            id,
            name: file.name,
            size: file.size,
            state: "queued",
            progress: 0,
          },
        ];
      }
    }
    this.#emit();
    this.#pump();
  }

  refuse(name: string, message: string): void {
    const id = this.#nextId++;
    this.#items = [
      ...this.#items,
      { id, name, size: 0, state: "failed", progress: 0, message },
    ];
    this.#announcement = `${name} was not added. ${message}`;
    this.#emit();
  }

  remove(id: number): void {
    this.#waiting = this.#waiting.filter((entry) => entry.id !== id);
    if (this.#active?.id === id) {
      // The rejection removes the row and starts the next file.
      this.#active.controller.abort();
      return;
    }
    this.#drop(id);
    this.#emit();
  }

  /** Leaving the page stops what is on its way: nobody is left to show its result. */
  stop(): void {
    this.#stopped = true;
    this.#waiting = [];
    this.#active?.controller.abort();
  }

  #emit(): void {
    if (this.#stopped) return;
    this.#onChange({ items: this.#items, announcement: this.#announcement });
  }

  #patch(id: number, change: Partial<UploadItem>): void {
    this.#items = this.#items.map((item) =>
      item.id === id ? { ...item, ...change } : item,
    );
  }

  #drop(id: number): void {
    this.#items = this.#items.filter((item) => item.id !== id);
  }

  #pump(): void {
    if (this.#active || this.#stopped) return;
    const next = this.#waiting.shift();
    if (!next) return;
    const { id, file } = next;
    const controller = new AbortController();
    this.#active = { id, controller };
    this.#patch(id, { state: "uploading" });
    this.#announcement = `Sending ${file.name}.`;
    this.#emit();
    uploadDocument(file, {
      signal: controller.signal,
      onProgress: (progress) => {
        this.#patch(id, { progress });
        this.#emit();
      },
    })
      .then(
        (document) => {
          const sentence = storedSentence(file.name, document);
          if (document.duplicate)
            this.#patch(id, {
              state: "duplicate",
              progress: 1,
              // The row already shows the file's name: only what it is called there.
              message:
                document.refused ??
                (document.name === file.name
                  ? "The same file is already in your documents."
                  : `The same file is already in your documents, as ${document.name}.`),
            });
          else this.#drop(id);
          this.#announcement = sentence;
          if (!this.#stopped) this.#events.onStored(document);
        },
        (error: unknown) => {
          if (isApiError(error) && error.kind === "aborted") {
            this.#drop(id);
            if (this.#stopped) return;
            // True whether or not the last bytes had left: nothing is claimed about the file.
            this.#announcement = `Stopped sending ${file.name}.`;
            this.#events.onCancelled();
            return;
          }
          const message = failureSentence(error);
          this.#patch(id, { state: "failed", message });
          this.#announcement = `${file.name} was not added. ${message}`;
        },
      )
      .finally(() => {
        this.#active = undefined;
        this.#emit();
        this.#pump();
      });
  }
}

/** The upload queue of a page: its state, and the three things the page can ask of it. */
export function useUploads(events: UploadEvents): Uploads {
  const [state, setState] = useState<UploadsState>({
    items: [],
    announcement: "",
  });
  const latest = useRef(events);
  useEffect(() => {
    latest.current = events;
  }, [events]);
  const queue = useRef<UploadQueue>();
  useEffect(() => {
    const created = new UploadQueue(setState, {
      onStored: (document) => {
        latest.current.onStored(document);
      },
      onCancelled: () => {
        latest.current.onCancelled();
      },
    });
    queue.current = created;
    return () => {
      created.stop();
    };
  }, []);

  const add = useCallback((files: File[], fileBytes: number) => {
    queue.current?.add(files, fileBytes);
  }, []);
  const refuse = useCallback((name: string, message: string) => {
    queue.current?.refuse(name, message);
  }, []);
  const remove = useCallback((id: number) => {
    queue.current?.remove(id);
  }, []);
  return { ...state, add, refuse, remove };
}
