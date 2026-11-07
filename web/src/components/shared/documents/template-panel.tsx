import { FileText, LayoutGrid } from "lucide-react";
import { Link } from "react-router";

import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { PanelProps } from "./document-view";
import {
  failureSentence,
  type DocumentOut,
  type Kind,
} from "@/features/documents/api/documents-api";
import { type Template } from "@/features/documents/api/templates-api";
import {
  useTemplates,
  useTemplateSwitch,
} from "@/features/documents/api/use-templates";
import { useVersions } from "@/features/documents/api/use-version-history";
import { formatWhen } from "@/features/documents/helpers/labels";
import {
  galleryPath,
  inheritanceSentence,
  isRemoved,
  nameOf,
  ordered,
  REMOVED_SENTENCE,
  thumbnailOf,
} from "@/features/documents/helpers/template-choice";

/**
 * The template of a document, the last section of the document view: a strip (a Carousel) of the
 * templates' thumbnails, the person's own first, the current one marked; a click switches (a new version
 * with the same words, rendered). It says what the switch changes, keeps a sent version as it
 * was, and "See all" opens the gallery for this document. A page that runs over one page is said
 * above the page itself (fit-note.tsx).
 */
export function TemplatePanel({ document }: PanelProps) {
  const templates = useTemplates();
  const history = useVersions(document);
  const choose = useTemplateSwitch(document);

  const sent = (history.data ?? [])
    .filter((one) => one.frozen_at !== null)
    .sort((a, b) => b.number - a.number)[0];
  const drawing = choose.isSuccess && document.render.state !== "idle";

  return (
    <Section
      title="Template"
      description={inheritanceSentence(document)}
      actions={
        <Button asChild variant="outline" size="sm">
          <Link to={galleryPath(document.id)}>
            <LayoutGrid aria-hidden="true" />
            See all
          </Link>
        </Button>
      }
    >
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
      {drawing ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" role={undefined} />
          Drawing the page in its new template
        </p>
      ) : null}
      {choose.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{failureSentence(choose.error)}</AlertDescription>
        </Alert>
      ) : null}
      {templates.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" role={undefined} />
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
          busy={choose.isPending}
          onChoose={(slug) => {
            choose.mutate(slug);
          }}
        />
      )}
    </Section>
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
    <Carousel
      opts={{ align: "start" }}
      aria-label="Template choice"
      data-testid="template-strip"
    >
      <CarouselContent role="list" aria-label="Templates" className="py-1">
        {templates.map((template) => {
          const name = nameOf(template, language);
          const chosen = template.slug === current;
          return (
            <CarouselItem
              key={template.slug}
              role="listitem"
              aria-roledescription={undefined}
              className="basis-36"
            >
              <Button
                type="button"
                variant="outline"
                aria-pressed={chosen}
                disabled={busy}
                onClick={() => {
                  if (!chosen) onChoose(template.slug);
                }}
                className={cn(
                  "h-auto w-full flex-col items-stretch justify-start gap-1.5 rounded-xl p-1.5 text-left font-normal whitespace-normal disabled:opacity-60",
                  chosen
                    ? "border-primary ring-2 ring-primary dark:border-primary"
                    : "border-border",
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
              </Button>
            </CarouselItem>
          );
        })}
      </CarouselContent>
      <CarouselPrevious className="left-1 bg-card disabled:invisible" />
      <CarouselNext className="right-1 bg-card disabled:invisible" />
    </Carousel>
  );
}

/** Page 1 of a template at the page's own ratio, or a plain frame for one with no image (the person's own). */
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
  return (
    <AspectRatio
      ratio={210 / 297}
      className={cn(
        "overflow-hidden rounded-sm border",
        address === undefined
          ? "flex flex-col items-center justify-center gap-2 border-dashed bg-muted text-muted-foreground"
          : "bg-white",
        className,
      )}
      role={address === undefined && alt !== "" ? "img" : undefined}
      aria-label={address === undefined && alt !== "" ? alt : undefined}
    >
      {address === undefined ? (
        <>
          <FileText aria-hidden="true" className="size-6" />
          {alt === "" ? null : (
            <span aria-hidden="true" className="text-xs">
              No preview
            </span>
          )}
        </>
      ) : (
        <img
          src={address}
          alt={alt}
          loading="lazy"
          className="size-full object-cover object-top"
        />
      )}
    </AspectRatio>
  );
}
