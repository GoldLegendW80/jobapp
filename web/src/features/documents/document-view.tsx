import { useQuery } from "@tanstack/react-query";
import { LoaderCircle, RotateCw } from "lucide-react";
import { useEffect, useRef, useState, type ComponentType } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { describeError } from "@/lib/api";

import {
  documentKey,
  getDocument,
  type DocumentOut,
  type Generation,
  type GenerationBrief,
} from "./documents-api";
import * as chat from "./chat-panel";
import * as compare from "./compare-panel";
import * as edit from "./edit-panel";
import { SeePanel } from "./see-panel";
import * as template from "./template-panel";
import { isOpen, pollInterval, templateLabel } from "./labels";

/** What every panel is given: the document as the api last answered, and its generation if known. */
export interface PanelProps {
  document: DocumentOut;
  generation: Generation | GenerationBrief | null;
}

interface View {
  id: "see" | "edit" | "chat" | "template" | "compare";
  label: string;
  available: boolean;
  Panel: ComponentType<PanelProps>;
}

/** The five views; a placeholder that is not available yet is hidden. */
const VIEWS: View[] = [
  { id: "see", label: "See", available: true, Panel: SeePanel },
  {
    id: "edit",
    label: "Edit",
    available: edit.available,
    Panel: edit.EditPanel,
  },
  {
    id: "chat",
    label: "Chat",
    available: chat.available,
    Panel: chat.ChatPanel,
  },
  {
    id: "template",
    label: "Template",
    available: template.available,
    Panel: template.TemplatePanel,
  },
  {
    id: "compare",
    label: "Compare",
    available: compare.available,
    Panel: compare.ComparePanel,
  },
];

/**
 * One document, a base CV or a posting's CV or letter: its header and its views. The same
 * component serves /profile/cv/:id and (D12) /jobs/:id/documents/:kind. `generation` is what the
 * caller knows of the document's latest generation (the list of base CVs, or the posting's
 * card); while it is open the document is read again every POLL_MS (WAITING_POLL_MS while it waits for the
 * person), and once more when it ends.
 */
export function DocumentView({
  documentId,
  generation = null,
}: {
  documentId: string;
  generation?: Generation | GenerationBrief | null;
}) {
  const [view, setView] = useState<View["id"]>("see");
  const query = useQuery({
    queryKey: documentKey(documentId),
    queryFn: ({ signal }) => getDocument(documentId, signal),
    refetchInterval: (current) =>
      pollInterval(current.state.data?.render.state ?? "idle", generation),
  });

  // The generation prop comes from another poll than the document's own: when it ends between two
  // reads of the document, the pending timer is dropped with the last read kept. Read once more.
  const wasOpen = useRef(isOpen(generation));
  const { refetch } = query;
  const open = isOpen(generation);
  useEffect(() => {
    if (wasOpen.current && !open) void refetch();
    wasOpen.current = open;
  }, [open, refetch]);

  if (query.isPending) {
    return (
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading the document
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="flex flex-col gap-3">
        <Alert variant="destructive">
          <AlertTitle>The document could not be loaded</AlertTitle>
          <AlertDescription>{describeError(query.error)}</AlertDescription>
        </Alert>
        <div>
          <Button
            variant="outline"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  const document = query.data;
  const views = VIEWS.filter((one) => one.available);
  return (
    <div className="flex flex-col gap-4" data-testid="document-view">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Badge variant="secondary">
          {templateLabel(document.template_slug)}
        </Badge>
        <Badge variant="outline">
          {document.language === "fr" ? "French" : "English"}
        </Badge>
      </div>
      <Tabs
        value={view}
        onValueChange={(value) => {
          const next = views.find((one) => one.id === value);
          if (next) setView(next.id);
        }}
      >
        {views.length > 1 ? (
          <TabsList aria-label="Views of this document">
            {views.map((one) => (
              <TabsTrigger key={one.id} value={one.id}>
                {one.label}
              </TabsTrigger>
            ))}
          </TabsList>
        ) : null}
        {views.map((one) => (
          <TabsContent key={one.id} value={one.id}>
            <one.Panel document={document} generation={generation} />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
