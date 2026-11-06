import { describeError } from "@/lib/api";

import { useVersionActions } from "@/features/documents/api/use-version-actions";
import { useVersionHistory } from "@/features/documents/api/use-version-history";
import { isOpen } from "@/features/documents/helpers/labels";
import type { PanelProps } from "./document-view";
import { DocumentStatus } from "./see-panel/document-status";
import { RestoreVersionDialog } from "./see-panel/restore-version-dialog";
import { VersionCorrections } from "./see-panel/version-corrections";
import { VersionHistory } from "./see-panel/version-history";
import { VersionPreview } from "./see-panel/version-preview";

/**
 * The See view: the page, whether it fits one page, the corrections and the version history,
 * where a version is restored into the draft or approved.
 */
export function SeePanel({ document, generation }: PanelProps) {
  const { history, rows, shown, latestNumber, choose } =
    useVersionHistory(document);
  const actions = useVersionActions(document);
  const open = isOpen(generation);
  const rendering = document.render.state !== "idle";

  return (
    <div className="flex flex-col gap-6">
      <DocumentStatus
        open={open}
        rendering={rendering}
        generation={generation}
        renderMessage={document.render.message}
        kind={document.kind}
        hasVersion={shown !== undefined}
      />
      {shown === undefined ? (
        open ? null : (
          <p className="text-sm text-muted-foreground">
            Nothing has been written for this document yet.
          </p>
        )
      ) : (
        <VersionPreview
          documentId={document.id}
          version={shown}
          isLatest={shown.number === latestNumber}
          rendering={rendering}
        />
      )}
      {shown !== undefined && shown.corrections_count > 0 ? (
        <VersionCorrections documentId={document.id} version={shown} />
      ) : null}
      <VersionHistory
        rows={rows}
        loading={history.isPending}
        error={history.isError ? describeError(history.error) : undefined}
        shown={shown?.number}
        onChoose={choose}
        busy={actions.busy}
        onRestore={actions.askRestore}
        onApprove={(number) => {
          actions.approve.mutate(number);
        }}
      />
      <RestoreVersionDialog
        version={actions.restoring}
        busy={actions.restore.isPending}
        onCancel={() => {
          actions.askRestore(undefined);
        }}
        onRestore={(number) => {
          actions.restore.mutate(number);
        }}
      />
    </div>
  );
}
