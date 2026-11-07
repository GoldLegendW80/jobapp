import { Badge } from "@/components/ui/badge";

import type { VersionSummary } from "@/features/documents/api/documents-api";
import { formatWhen } from "@/features/documents/helpers/labels";

/** What marks the version shown: when it was sent, and a newer page being drawn. */
export function VersionMeta({
  version,
  drawing,
}: {
  version: VersionSummary | undefined;
  drawing: boolean;
}) {
  const sent = version?.frozen_at ?? null;
  if (sent === null && !drawing) return null;
  return (
    <span className="flex flex-wrap items-center gap-2">
      {sent === null ? null : (
        <Badge variant="outline">Sent on {formatWhen(sent)}</Badge>
      )}
      {drawing ? <span>A newer page is being drawn</span> : null}
    </span>
  );
}
