import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, LayoutGrid, LoaderCircle, Wand2 } from "lucide-react";
import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { PanelProps } from "./document-view";
import {
  documentKey,
  failureSentence,
  fitDocument,
  listTemplates,
  listVersions,
  postingDocumentsKey,
  templatesKey,
  versionsKey,
  type DocumentOut,
  type Kind,
  type Template,
} from "./documents-api";
import { formatWhen, isOpen } from "./labels";
import {
  canShorten,
  galleryPath,
  inheritanceSentence,
  isRemoved,
  nameOf,
  ordered,
  REMOVED_SENTENCE,
  thumbnailOf,
  useTemplateSwitch,
} from "./template-choice";

export const available = true;

/**
 * The Template view: a strip of the templates' thumbnails, the person's own first, the current
 * one marked; a click switches (a new version with the same words, rendered). It says what the
 * switch changes, keeps a sent version as it was, offers "Make it fit" when the page overflows,
 * and "See all" opens the gallery for this document.
 */
export function TemplatePanel({ document, generation }: PanelProps) {
  const client = useQueryClient();
  const templates = useQuery({
    queryKey: templatesKey,
    queryFn: ({ signal }) => listTemplates(signal),
  });
  const latestNumber = document.latest?.number;
  const history = useQuery({
    // The same key as the See view's history, so both read one list.
    queryKey: [...versionsKey(document.id), latestNumber ?? 0],
    queryFn: ({ signal }) => listVersions(document.id, signal),
  });
  const choose = useTemplateSwitch(document);
  const shorten = useMutation({
    mutationFn: () => fitDocument(document.id),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: documentKey(document.id) });
      if (document.posting_id !== null)
        void client.invalidateQueries({
          queryKey: postingDocumentsKey(document.posting_id),
        });
    },
  });

  const sent = (history.data ?? [])
    .filter((one) => one.frozen_at !== null)
    .sort((a, b) => b.number - a.number)[0];
  const rendering = document.render.state !== "idle";
  const overflow = document.latest?.fit === "overflow" && !rendering;
  const busy = choose.isPending || shorten.isPending;
  const failure = choose.isError
    ? failureSentence(choose.error)
    : shorten.isError
      ? failureSentence(shorten.error)
      : undefined;

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {inheritanceSentence(document)}
      </p>
      {sent?.frozen_at ? (
        <p className="text-sm text-muted-foreground" data-testid="sent-note">
          Version {sent.number} was sent on {formatWhen(sent.frozen_at)}. It
          stays as it was: a new template makes a new version.
        </p>
      ) : null}
      {isRemoved(document.template_slug, templates.data) ? (
        <Alert data-testid="removed-note">
          <AlertTitle>Template no longer available</AlertTitle>
          <AlertDescription>{REMOVED_SENTENCE}</AlertDescription>
        </Alert>
      ) : null}
      {rendering ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Drawing the page
        </p>
      ) : null}
      {overflow ? (
        <Overflow
          document={document}
          offer={canShorten(document) && !isOpen(generation)}
          busy={busy}
          onShorten={() => {
            shorten.mutate();
          }}
        />
      ) : null}
      {shorten.isSuccess ? (
        <p role="status" className="text-sm text-muted-foreground">
          A shorter version is being written. It shows in See when it is ready.
        </p>
      ) : null}
      {failure ? (
        <Alert variant="destructive">
          <AlertDescription>{failure}</AlertDescription>
        </Alert>
      ) : null}
      {templates.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the templates
        </p>
      ) : templates.isError ? (
        <Alert variant="destructive">
          <AlertTitle>The templates could not be loaded</AlertTitle>
          <AlertDescription>{describeError(templates.error)}</AlertDescription>
        </Alert>
      ) : (
        <Strip
          templates={ordered(templates.data)}
          current={document.template_slug}
          kind={document.kind}
          language={document.language}
          busy={busy}
          onChoose={(slug) => {
            choose.mutate(slug);
          }}
        />
      )}
      <div>
        <Button asChild variant="outline" size="sm">
          <Link to={galleryPath(document.id)}>
            <LayoutGrid aria-hidden="true" />
            See all
          </Link>
        </Button>
      </div>
    </div>
  );
}

function Overflow({
  document,
  offer,
  busy,
  onShorten,
}: {
  document: DocumentOut;
  offer: boolean;
  busy: boolean;
  onShorten: () => void;
}) {
  return (
    <Alert variant="destructive" data-testid="overflow-note">
      <AlertTitle>Longer than one page</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <p>
          {offer
            ? "With this template the CV does not fit one page. Make it fit asks the writer for a shorter version, or choose a more compact template."
            : `With this template the ${document.kind === "cv" ? "CV" : "letter"} does not fit one page. Shorten the longest fields in Edit, or choose a more compact template.`}
        </p>
        {offer ? (
          <Button
            size="sm"
            variant="secondary"
            className="w-fit"
            disabled={busy}
            onClick={onShorten}
          >
            <Wand2 aria-hidden="true" />
            Make it fit
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

function Strip({
  templates,
  current,
  kind,
  language,
  busy,
  onChoose,
}: {
  templates: Template[];
  current: string;
  kind: Kind;
  language: DocumentOut["language"];
  busy: boolean;
  onChoose: (slug: string) => void;
}) {
  return (
    <ul
      aria-label="Templates"
      className="flex snap-x gap-3 overflow-x-auto pb-2"
      data-testid="template-strip"
    >
      {templates.map((template) => {
        const name = nameOf(template, language);
        const chosen = template.slug === current;
        return (
          <li key={template.slug} className="shrink-0 snap-start">
            <button
              type="button"
              aria-pressed={chosen}
              disabled={busy}
              onClick={() => {
                if (!chosen) onChoose(template.slug);
              }}
              className={cn(
                "flex w-32 flex-col gap-1 rounded-md border p-1 text-left text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60",
                chosen ? "border-primary ring-2 ring-primary" : "border-border",
              )}
            >
              <Thumbnail template={template} kind={kind} />
              <span className="truncate font-medium">{name}</span>
              {template.user ? (
                <span className="text-xs text-muted-foreground">
                  From your CV
                </span>
              ) : null}
              {chosen ? (
                <Badge variant="secondary" className="w-fit">
                  In use
                </Badge>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Page 1 of a template, or a plain frame for one with no image (the person's own). */
export function Thumbnail({
  template,
  kind,
  alt = "",
  className,
}: {
  template: Template;
  kind: Kind;
  /** Empty where the button around it is named. */
  alt?: string;
  className?: string;
}) {
  const address = thumbnailOf(template, kind);
  if (address === undefined)
    return (
      <span
        role={alt === "" ? undefined : "img"}
        aria-label={alt === "" ? undefined : alt}
        className={cn(
          "flex aspect-[210/297] w-full items-center justify-center rounded-sm bg-muted text-muted-foreground",
          className,
        )}
      >
        <FileText aria-hidden="true" className="size-6" />
      </span>
    );
  return (
    <img
      src={address}
      alt={alt}
      loading="lazy"
      className={cn(
        "aspect-[210/297] w-full rounded-sm border bg-white object-cover object-top",
        className,
      )}
    />
  );
}
