import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Toggle } from "@/components/ui/toggle";
import { cn } from "@/lib/utils";

import type { VersionSummary } from "@/features/documents/api/documents-api";
import {
  fitLabel,
  formatWhen,
  ORIGIN_LABELS,
  templateLabel,
} from "@/features/documents/helpers/labels";

/** The versions of a document, newest first, each shown, restored into the draft or approved. */
export function VersionHistory({
  rows,
  loading,
  error,
  shown,
  description,
  onChoose,
  busy,
  onRestore,
  onApprove,
}: {
  rows: VersionSummary[];
  loading: boolean;
  error: string | undefined;
  shown: number | undefined;
  description?: string;
  onChoose: (number: number) => void;
  busy: boolean;
  onRestore: (version: VersionSummary) => void;
  onApprove: (number: number) => void;
}) {
  if (rows.length === 0 && !loading && error === undefined) return null;
  return (
    <Section title="Versions" description={description}>
      {error !== undefined ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : loading && rows.length === 0 ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the versions
        </p>
      ) : (
        <Card className="gap-0 py-0">
          <ul className="flex flex-col divide-y">
            {rows.map((one) => {
              const current = one.number === shown;
              return (
                <li
                  key={one.number}
                  className={cn(
                    "flex flex-col gap-3 p-4 first:rounded-t-xl last:rounded-b-xl",
                    current && "bg-muted/60",
                  )}
                >
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <p className="text-sm font-medium">
                      Version {one.number}, {ORIGIN_LABELS[one.origin]}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {formatWhen(one.created_at)},{" "}
                      {templateLabel(one.template_slug)}, {fitLabel(one)}
                      {one.approved_at !== null ? ", approved" : ""}
                      {one.frozen_at !== null
                        ? `, sent on ${formatWhen(one.frozen_at)}`
                        : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Toggle
                      variant="outline"
                      size="sm"
                      className="px-3.5"
                      pressed={current}
                      aria-label={
                        current
                          ? `Shown, version ${String(one.number)}`
                          : `Open version ${String(one.number)}`
                      }
                      onPressedChange={(on) => {
                        // One version is always shown: pressing it again changes nothing.
                        if (on) onChoose(one.number);
                      }}
                    >
                      {current ? "Shown" : "Open"}
                    </Toggle>
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`Restore version ${String(one.number)}`}
                      disabled={busy}
                      onClick={() => {
                        onRestore(one);
                      }}
                    >
                      Restore
                    </Button>
                    {one.approved_at === null && one.frozen_at === null ? (
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Approve version ${String(one.number)}`}
                        disabled={busy}
                        onClick={() => {
                          onApprove(one.number);
                        }}
                      >
                        Approve
                      </Button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </Section>
  );
}
