/**
 * A count after the label of a link of the main navigation: an orange badge, and the same number
 * in `words` for a screen reader, read in the link's name ("Activity, 2 under way"). Nothing at
 * zero.
 */
export function CountBadge({ count, words }: { count: number; words: string }) {
  if (count <= 0) return null;
  return (
    <>
      <span
        aria-hidden="true"
        className="ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-brand px-1.5 text-xs font-semibold text-brand-foreground tabular-nums"
      >
        {count > 99 ? "99+" : count}
      </span>
      <span className="sr-only">
        , {count} {words}
      </span>
    </>
  );
}
