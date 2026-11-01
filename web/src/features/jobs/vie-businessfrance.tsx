/**
 * Business France VIE in Find jobs (owner, 2026-11-01): one box, off unless ticked. A sweep then
 * reads the VIE offers through the site's own public search API (api
 * app/browser/sources/vie_businessfrance.py), without a browser and without signing in.
 */
import { useId } from "react";

/** The sentence beside the box: what it reads, and that it needs no sign in. */
export const VIE_SENTENCE =
  "Reads the VIE offers of mon-vie-via.businessfrance.fr for your saved titles and countries, through the site's public search, without signing in.";

export function VieChoice({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (next: boolean) => void;
}) {
  const id = useId();
  return (
    <section className="space-y-3" aria-labelledby={`${id}-vie`}>
      <h3 id={`${id}-vie`} className="font-medium">
        Business France VIE
      </h3>
      <div className="min-w-0 space-y-1 rounded-md border p-3 text-sm">
        <label className="flex min-w-0 items-center gap-2 font-medium">
          <input
            type="checkbox"
            className="size-4 shrink-0 accent-primary"
            checked={value}
            aria-describedby={`${id}-vie-sentence`}
            onChange={(event) => {
              onChange(event.target.checked);
            }}
          />
          Business France VIE
        </label>
        <p
          id={`${id}-vie-sentence`}
          className="text-xs text-muted-foreground break-words"
        >
          {VIE_SENTENCE}
        </p>
      </div>
    </section>
  );
}
