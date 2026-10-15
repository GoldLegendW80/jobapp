import { LoaderCircle } from "lucide-react";

/**
 * Where the live view of the browser goes while the assistant fills the form (browser B4). Until
 * it is merged this shows the state in words; the "before" screenshot appears below when the fill
 * ends. B4 replaces the body of this component, nothing around it changes.
 */
export function LiveViewSlot({ waiting }: { waiting: boolean }) {
  return (
    <div
      data-testid="live-view-slot"
      className="flex min-h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground"
    >
      <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
      <p>
        {waiting
          ? "The browser starts soon."
          : "The assistant is filling the form."}
      </p>
      <p>A screenshot of the filled form appears here when it is done.</p>
    </div>
  );
}
