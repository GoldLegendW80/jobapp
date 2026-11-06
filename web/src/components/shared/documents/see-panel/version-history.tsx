import { Fragment } from "react";

import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";

import type { VersionSummary } from "@/features/documents/api/documents-api";
import {
  fitLabel,
  formatWhen,
  ORIGIN_LABELS,
  templateLabel,
} from "@/features/documents/helpers/labels";

/** The versions of a document, each opened, restored into the draft or approved from here. */
export function VersionHistory({
  rows,
  loading,
  error,
  shown,
  onChoose,
  busy,
  onRestore,
  onApprove,
}: {
  rows: VersionSummary[];
  loading: boolean;
  error: string | undefined;
  shown: number | undefined;
  onChoose: (number: number) => void;
  busy: boolean;
  onRestore: (version: VersionSummary) => void;
  onApprove: (number: number) => void;
}) {
  if (rows.length === 0 && !loading && error === undefined) return null;
  return (
    <section aria-labelledby="history-heading" className="flex flex-col gap-2">
      <h3 id="history-heading" className="text-sm font-semibold">
        Versions
      </h3>
      {error !== undefined ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : loading && rows.length === 0 ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the versions
        </p>
      ) : (
        <ItemGroup className="rounded-lg border">
          {rows.map((one, index) => (
            <Fragment key={one.number}>
              {index > 0 ? <ItemSeparator /> : null}
              <Item
                role="listitem"
                className="justify-between gap-2 rounded-none p-3"
              >
                <ItemContent className="min-w-0 flex-auto gap-0.5">
                  <ItemTitle>
                    Version {one.number}, {ORIGIN_LABELS[one.origin]}
                  </ItemTitle>
                  <ItemDescription className="line-clamp-none text-wrap">
                    {formatWhen(one.created_at)},{" "}
                    {templateLabel(one.template_slug)}, {fitLabel(one)}
                    {one.approved_at !== null ? ", approved" : ""}
                    {one.frozen_at !== null
                      ? `, sent on ${formatWhen(one.frozen_at)}`
                      : ""}
                  </ItemDescription>
                </ItemContent>
                <ItemActions className="flex-wrap">
                  <Button
                    variant={one.number === shown ? "secondary" : "outline"}
                    size="sm"
                    aria-pressed={one.number === shown}
                    aria-label={
                      one.number === shown
                        ? `Shown, version ${String(one.number)}`
                        : `Open version ${String(one.number)}`
                    }
                    onClick={() => {
                      onChoose(one.number);
                    }}
                  >
                    {one.number === shown ? "Shown" : "Open"}
                  </Button>
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
                </ItemActions>
              </Item>
            </Fragment>
          ))}
        </ItemGroup>
      )}
    </section>
  );
}
