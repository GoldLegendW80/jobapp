import { useEffect, useRef, type ComponentProps, type ReactNode } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { useIsPhone } from "@/hooks/use-media-query";

import {
  type DocumentOut,
  type Generation,
} from "@/features/documents/api/documents-api";
import {
  useLiveDocument,
  useRefreshCaptured,
} from "@/features/documents/api/use-document";
import { useVersionActions } from "@/features/documents/api/use-version-actions";
import { useVersionHistory } from "@/features/documents/api/use-version-history";
import { type GenerationBrief } from "@/features/documents/api/posting-documents-api";
import { describeError } from "@/lib/api";
import * as compare from "./compare-panel";
import { ChatSection, ChatSheet } from "./document-view/chat-section";
import { DocumentFacts } from "./document-view/document-facts";
import { DocumentStatus } from "./document-view/document-status";
import { FitNote } from "./document-view/fit-note";
import { RestoreVersionDialog } from "./document-view/restore-version-dialog";
import { VersionCorrections } from "./document-view/version-corrections";
import { VersionHistory } from "./document-view/version-history";
import { PdfActions, PrintedPage } from "./document-view/printed-page";
import { VersionMeta } from "./document-view/version-heading";
import { VersionSheet } from "./document-view/version-sheet";
import { EditPanel } from "./edit-panel";
import { PanelPanel } from "./panel-panel";
import { TemplatePanel } from "./template-panel";
import { isOpen } from "@/features/documents/helpers/labels";
import { versionTitle } from "@/features/documents/helpers/versions";

/** What every part of the view is given: the document as the api last answered, and its generation if known. */
export interface PanelProps {
  document: DocumentOut;
  generation: Generation | GenerationBrief | null;
}

/**
 * The page header of a document shown on a page of its own. It holds the PDF of the version
 * shown; `actions` are the caller's own, after it (more than one goes in a menu).
 */
type Header = Omit<ComponentProps<typeof PageHeader>, "actions"> & {
  actions?: ReactNode;
};

/**
 * One document, a base CV or a posting's CV or letter, whole on one page: its page, edited where
 * it is read, what the fact check corrected, its template and its hiring panel in the main
 * column; the chat, the versions, the printed page and the facts in the side rail. The same
 * component serves /profile/cv/:id and /jobs/:id/documents/:kind; with `header` it draws the
 * page's header, which holds the PDF of the version shown and, on a phone, opens the chat.
 * `generation` is what the caller knows of the document's latest generation (the list of base
 * CVs, or the posting's card); while it is open the document is read again every POLL_MS
 * (WAITING_POLL_MS while it waits for the person), and once more when it ends.
 */
export function DocumentView({
  documentId,
  generation = null,
  capture = null,
  header,
}: {
  documentId: string;
  generation?: Generation | GenerationBrief | null;
  capture?: Generation | null;
  header?: Header;
}) {
  const query = useLiveDocument(documentId, generation);
  const refreshCaptured = useRefreshCaptured(documentId);

  // The generation prop comes from another poll than the document's own: when it ends between two
  // reads of the document, the pending timer is dropped with the last read kept. Read once more.
  const wasOpen = useRef(isOpen(generation));
  const { refetch } = query;
  const open = isOpen(generation);
  useEffect(() => {
    if (wasOpen.current && !open) void refetch();
    wasOpen.current = open;
  }, [open, refetch]);

  // Capture can end long after content did, so it needs its own final read.
  const captureWasOpen = useRef(isOpen(capture));
  const captureOpen = isOpen(capture);
  useEffect(() => {
    if (captureWasOpen.current && !captureOpen) {
      void refetch();
      if (capture?.status === "done") refreshCaptured();
    }
    captureWasOpen.current = captureOpen;
  }, [captureOpen, capture?.status, refreshCaptured, refetch]);

  if (query.isPending || query.isError) {
    return (
      <>
        {header ? <PageHeader {...header} /> : null}
        {query.isPending ? (
          <LoadingCard>Loading the document</LoadingCard>
        ) : (
          <LoadError query={query}>The document could not be loaded.</LoadError>
        )}
      </>
    );
  }
  return (
    <LoadedDocument
      document={query.data}
      generation={generation}
      header={header}
    />
  );
}

function LoadedDocument({
  document,
  generation,
  header,
}: PanelProps & { header: Header | undefined }) {
  const { history, rows, shown, latestNumber, choose } =
    useVersionHistory(document);
  const actions = useVersionActions(document);
  // On a phone the rail comes last, under the whole form: the header opens the chat instead.
  const chatInHeader = useIsPhone() && header !== undefined;
  const top = useRef<HTMLDivElement>(null);
  const panel = { document, generation };
  const isLatest = shown === undefined || shown.number === latestNumber;
  const heading = {
    title: versionTitle(shown, isLatest),
    meta: (
      <VersionMeta
        version={shown}
        drawing={isLatest && document.render.state !== "idle"}
      />
    ),
    fit:
      shown === undefined ? null : (
        <FitNote {...panel} version={shown} isLatest={isLatest} />
      ),
    below:
      shown !== undefined && shown.corrections_count > 0 ? (
        <VersionCorrections documentId={document.id} version={shown} />
      ) : null,
  };
  const pdf =
    header && shown?.has_pdf ? (
      <PdfActions documentId={document.id} version={shown} primary />
    ) : null;
  const headerActions =
    pdf || chatInHeader || header?.actions ? (
      <>
        {pdf}
        {chatInHeader ? <ChatSheet {...panel} /> : null}
        {header?.actions}
      </>
    ) : undefined;

  return (
    <>
      {header ? <PageHeader {...header} actions={headerActions} /> : null}
      <div data-testid="document-view">
        <PageBody
          aside={
            <>
              {chatInHeader ? null : <ChatSection {...panel} />}
              <VersionHistory
                rows={rows}
                loading={history.isPending}
                error={
                  history.isError ? describeError(history.error) : undefined
                }
                shown={shown?.number}
                description={
                  document.scope === "posting"
                    ? "An application sends the approved version, else the latest."
                    : undefined
                }
                onChoose={(number) => {
                  choose(number);
                  // On a narrow screen the versions are under the form: bring the page back in view.
                  const page = top.current;
                  if (page && page.getBoundingClientRect().top < 0)
                    page.scrollIntoView({ block: "start" });
                }}
                busy={actions.busy}
                onRestore={actions.askRestore}
                onApprove={(number) => {
                  actions.approve.mutate(number);
                }}
              />
              {shown === undefined ? null : (
                <PrintedPage
                  documentId={document.id}
                  version={shown}
                  pdf={!header}
                />
              )}
              <DocumentFacts document={document} shown={shown} />
            </>
          }
        >
          <div ref={top} className="flex scroll-mt-4 flex-col gap-8">
            <DocumentStatus
              open={isOpen(generation)}
              rendering={document.render.state !== "idle"}
              generation={generation}
              renderMessage={document.render.message}
              kind={document.kind}
              hasVersion={shown !== undefined}
            />
            {shown === undefined && !isOpen(generation) ? (
              <p className="text-sm text-muted-foreground">
                Nothing has been written for this document yet.
              </p>
            ) : null}
            {shown !== undefined && !isLatest ? (
              <VersionSheet
                document={document}
                version={shown}
                heading={heading}
              />
            ) : (
              <EditPanel {...panel} heading={heading} />
            )}
          </div>
          <TemplatePanel {...panel} />
          <PanelPanel {...panel} />
          {compare.available ? <compare.ComparePanel /> : null}
        </PageBody>
      </div>
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
    </>
  );
}
