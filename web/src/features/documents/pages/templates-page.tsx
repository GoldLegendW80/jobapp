import { Check, FileText, Mail, SearchX } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Toggle } from "@/components/ui/toggle";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { formatCount } from "@/lib/format";
import { useTitle } from "@/hooks/use-title";
import { WORDING } from "@/lib/wording";

import {
  failureSentence,
  type DocumentOut,
  type Language,
} from "../api/documents-api";
import { type Template } from "../api/templates-api";
import { useDocument } from "../api/use-document";
import { useTemplates, useTemplateSwitch } from "../api/use-templates";
import { Thumbnail } from "@/components/shared/documents/template-panel";
import {
  COLUMNS_OPTIONS,
  descriptionOf,
  documentPath,
  factsOf,
  galleryLanguage,
  isFiltered,
  matches,
  nameOf,
  NO_FILTERS,
  ordered,
  PHOTO_OPTIONS,
  thumbnailOf,
  type ColumnsFilter,
  type Filters,
  type PhotoFilter,
} from "../helpers/template-choice";

/**
 * /documents/templates: every template the api serves, the person's own under "From your CV"
 * first, with names, descriptions and what helps choose (columns, photo, easy for hiring
 * software to read), filters, and the letter on hover or tap. Opened from a document
 * (`?document=<id>`), each offers "Use for this document", which switches that document (a base
 * CV's template is what the CVs written from it afterwards take).
 */
export function TemplatesPage() {
  useTitle("Templates");
  const [params] = useSearchParams();
  const documentId = params.get("document") ?? "";
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const templates = useTemplates();
  const target = useDocument(documentId, { enabled: documentId !== "" });
  const document = target.data;
  const language = galleryLanguage(document?.language);
  const all = templates.data ? ordered(templates.data) : undefined;
  const shown = all?.filter((one) => matches(one, filters));

  return (
    <Page>
      <PageHeader
        trail={
          document
            ? [{ to: documentPath(document), label: crumbName(document) }]
            : undefined
        }
        title="Templates"
        description={
          document
            ? `Choose the look of ${documentName(document)}. The words stay the same.`
            : "The looks a CV and its letter can take. Open a document and choose Template to use one."
        }
      />
      <PageBody>
        {documentId !== "" && target.isError ? (
          <LoadError query={target}>
            The document this gallery was opened from could not be loaded, so no
            template can be used from here.
          </LoadError>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
          <FilterBar filters={filters} onChange={setFilters} />
          {all && shown ? (
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {isFiltered(filters)
                ? `${formatCount(shown.length)} of ${formatCount(all.length)} templates match.`
                : `${formatCount(all.length)} templates.`}
            </p>
          ) : null}
        </div>
        {templates.isPending ? (
          <GalleryLoading />
        ) : templates.isError ? (
          <LoadError query={templates}>
            The templates could not be loaded.
          </LoadError>
        ) : (
          <Gallery
            templates={all ?? []}
            shown={shown ?? []}
            language={language}
            document={document}
            onClear={() => {
              setFilters(NO_FILTERS);
            }}
          />
        )}
      </PageBody>
    </Page>
  );
}

/** The document the gallery was opened from, as the breadcrumb names it. */
function crumbName(document: DocumentOut): string {
  if (document.scope === "base") return document.name || "CV";
  return document.kind === "cv" ? "CV" : "Letter";
}

function documentName(document: DocumentOut): string {
  if (document.scope === "base")
    return document.name ? `the base CV "${document.name}"` : "this base CV";
  return document.kind === "cv" ? "this CV" : "this letter";
}

function FilterBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (next: Filters) => void;
}) {
  const photoId = useId();
  const columnsId = useId();
  const atsId = useId();
  return (
    <FieldSet className="flex-row flex-wrap items-center gap-x-6 gap-y-3">
      <FieldLegend className="sr-only">Filters</FieldLegend>
      <Field orientation="horizontal" className="w-auto gap-2">
        <FieldLabel htmlFor={photoId}>Photo</FieldLabel>
        <NativeSelect
          id={photoId}
          value={filters.photo}
          className="w-36"
          onChange={(event) => {
            onChange({
              ...filters,
              photo: event.target.value as PhotoFilter,
            });
          }}
        >
          {PHOTO_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field orientation="horizontal" className="w-auto gap-2">
        <FieldLabel htmlFor={columnsId}>Columns</FieldLabel>
        <NativeSelect
          id={columnsId}
          value={filters.columns}
          className="w-36"
          onChange={(event) => {
            onChange({
              ...filters,
              columns: event.target.value as ColumnsFilter,
            });
          }}
        >
          {COLUMNS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field orientation="horizontal" className="w-auto gap-2">
        <Checkbox
          id={atsId}
          checked={filters.ats}
          onCheckedChange={(checked) => {
            onChange({ ...filters, ats: checked === true });
          }}
        />
        <FieldLabel htmlFor={atsId}>{WORDING.easyForHiringSoftware}</FieldLabel>
      </Field>
    </FieldSet>
  );
}

const GRID =
  "grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6";

function Gallery({
  templates,
  shown,
  language,
  document,
  onClear,
}: {
  templates: Template[];
  shown: Template[];
  language: Language;
  document: DocumentOut | undefined;
  onClear: () => void;
}) {
  const choose = useTemplateSwitch(document);
  const own = shown.filter((one) => one.user);
  const bundled = shown.filter((one) => !one.user);
  const used =
    choose.isSuccess && document
      ? templates.find((one) => one.slug === choose.data.template_slug)
      : undefined;
  const card = (template: Template) => (
    <TemplateCard
      key={template.slug}
      template={template}
      language={language}
      document={document}
      busy={choose.isPending}
      onUse={() => {
        choose.mutate(template.slug);
      }}
    />
  );

  return (
    <>
      {used ? (
        <p role="status" className="text-sm font-medium">
          {nameOf(used, language)} is now used for this document.
          {document?.scope === "base"
            ? " The CVs you write from it afterwards take it; the CVs already written keep theirs."
            : ""}
        </p>
      ) : null}
      {choose.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{failureSentence(choose.error)}</AlertDescription>
        </Alert>
      ) : null}
      {shown.length === 0 ? (
        <EmptyState
          icon={SearchX}
          title="No template matches these filters"
          actions={
            <Button variant="outline" onClick={onClear}>
              Clear the filters
            </Button>
          }
        />
      ) : null}
      {own.length > 0 ? (
        <Section title="From your CV">
          <Grid>{own.map(card)}</Grid>
        </Section>
      ) : null}
      {bundled.length > 0 ? (
        <Section title="All templates">
          <Grid>{bundled.map(card)}</Grid>
        </Section>
      ) : null}
    </>
  );
}

function Grid({ children }: { children: ReactNode }) {
  return (
    <ul className={GRID} data-testid="template-grid">
      {children}
    </ul>
  );
}

/** The gallery's place while the templates load: grey cards in the same grid. */
function GalleryLoading() {
  return (
    <div role="status" className="flex flex-col gap-4">
      <span className="sr-only">Loading the templates</span>
      <Skeleton className="h-6 w-32" />
      <div aria-hidden="true" className={GRID}>
        {[0, 1, 2, 3, 4].map((one) => (
          <Card key={one} className="gap-3 p-3">
            <AspectRatio ratio={210 / 297}>
              <Skeleton className="size-full" />
            </AspectRatio>
            <Skeleton className="h-4 w-24" />
          </Card>
        ))}
      </div>
    </div>
  );
}

function TemplateCard({
  template,
  language,
  document,
  busy,
  onUse,
}: {
  template: Template;
  language: Language;
  document: DocumentOut | undefined;
  busy: boolean;
  onUse: () => void;
}) {
  const headingId = useId();
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const name = nameOf(template, language);
  const hasLetter = thumbnailOf(template, "letter") !== undefined;
  const letter = hasLetter && (hover || pinned);
  const inUse = document?.template_slug === template.slug;
  const description = descriptionOf(template, language);
  const toggle = pinned ? "See the CV" : "See the letter";

  return (
    <li
      aria-labelledby={headingId}
      className="flex min-w-0"
      data-testid={`template-${template.slug}`}
    >
      <Card className="w-full min-w-0 gap-3 p-3">
        <div className="relative">
          {/* A peek at the letter: the toggle below sits outside it, so it shows what it pins. */}
          <div
            onMouseEnter={() => {
              setHover(true);
            }}
            onMouseLeave={() => {
              setHover(false);
            }}
          >
            <Thumbnail
              template={template}
              kind={letter ? "letter" : "cv"}
              alt={letter ? `${name}, the letter` : `${name}, the CV`}
            />
          </div>
          {hasLetter ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Toggle
                  size="sm"
                  variant="outline"
                  className="absolute right-2 bottom-2 bg-card shadow-sm"
                  aria-label={toggle}
                  pressed={pinned}
                  onPressedChange={setPinned}
                >
                  {pinned ? (
                    <FileText aria-hidden="true" />
                  ) : (
                    <Mail aria-hidden="true" />
                  )}
                </Toggle>
              </TooltipTrigger>
              <TooltipContent>{toggle}</TooltipContent>
            </Tooltip>
          ) : null}
        </div>
        <div className="flex min-w-0 flex-col gap-1 px-1">
          <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
            <h3 id={headingId} className="font-medium">
              {name}
            </h3>
            {inUse ? (
              <Badge variant="success">
                <Check aria-hidden="true" />
                In use
              </Badge>
            ) : null}
          </div>
          {description ? (
            <p className="text-sm text-muted-foreground">{description}</p>
          ) : null}
          <p
            data-testid="template-facts"
            className="text-xs text-muted-foreground"
          >
            {factsOf(template)}
          </p>
        </div>
        {document && !inUse ? (
          <Button
            size="sm"
            variant="outline"
            className="mt-auto w-full"
            disabled={busy}
            onClick={onUse}
          >
            Use for this document
          </Button>
        ) : null}
      </Card>
    </li>
  );
}
