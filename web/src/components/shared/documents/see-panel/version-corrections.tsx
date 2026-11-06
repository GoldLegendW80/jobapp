import { describeError } from "@/lib/api";

import { type VersionSummary } from "@/features/documents/api/documents-api";
import { useVersion } from "@/features/documents/api/use-version-history";
import { fieldLabel } from "@/features/documents/helpers/labels";
import { correctionsOf } from "@/features/documents/helpers/versions";

/** What the fact check changed in a version: each field before and after, and why. */
export function VersionCorrections({
  documentId,
  version,
}: {
  documentId: string;
  version: VersionSummary;
}) {
  const query = useVersion(documentId, version.number);
  const entries = correctionsOf(query.data);
  return (
    <section
      aria-labelledby="corrections-heading"
      className="flex flex-col gap-2 rounded-lg border p-3"
    >
      <h3 id="corrections-heading" className="text-sm font-semibold">
        Corrected by the fact check ({version.corrections_count})
      </h3>
      <p className="text-sm text-muted-foreground">
        The writer checks every claim against your profile. What it could not
        support was changed or removed, and you can type it back by hand.
      </p>
      {query.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the corrections
        </p>
      ) : query.isError ? (
        <p className="text-sm text-destructive">{describeError(query.error)}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {entries.map((one, index) => (
            <li key={`${one.field_path}-${String(index)}`} className="text-sm">
              <p className="font-medium">{fieldLabel(one.field_path)}</p>
              <p>
                <span className="text-muted-foreground">Before: </span>
                {one.before}
              </p>
              <p>
                <span className="text-muted-foreground">After: </span>
                {one.after === "" ? "Removed" : one.after}
              </p>
              <p className="text-muted-foreground">
                {one.reason} (changed by{" "}
                {one.by === "code" ? "a rule" : "the writer"})
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
