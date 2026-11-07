import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";
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
    <Section
      title={`Corrected by the fact check (${String(version.corrections_count)})`}
      description="The writer checks every claim against your profile. What it could not support was changed or removed, and you can type it back by hand under Text."
    >
      <Card>
        <CardContent>
          {query.isPending ? (
            <p role="status" className="text-sm text-muted-foreground">
              Loading the corrections
            </p>
          ) : query.isError ? (
            <p className="text-sm text-destructive">
              {describeError(query.error)}
            </p>
          ) : (
            <ul className="flex flex-col gap-4">
              {entries.map((one, index) => (
                <li
                  key={`${one.field_path}-${String(index)}`}
                  className="flex flex-col gap-0.5 text-sm"
                >
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
        </CardContent>
      </Card>
    </Section>
  );
}
