import type { SaveState } from "@/features/profile/api/use-about-autosave";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

const WORDS: Record<Exclude<SaveState["state"], "failed">, string> = {
  saved: W.saved,
  saving: W.saving,
  waiting: W.waiting,
  conflict: W.conflictTitle,
};

/** Where the autosave stands, announced as it changes. */
export function SaveLine({ status }: { status: SaveState }) {
  return (
    <span
      aria-live="polite"
      className={
        status.state === "failed"
          ? "text-sm text-destructive"
          : "text-sm text-muted-foreground"
      }
    >
      {status.state === "failed"
        ? `${W.notSaved} ${status.message}`
        : WORDS[status.state]}
    </span>
  );
}
