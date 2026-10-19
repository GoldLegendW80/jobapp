import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, LoaderCircle, RotateCw } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import {
  documentKey,
  failureSentence,
  getDocument,
  listTemplates,
  templatesKey,
  type DocumentOut,
  type Language,
  type Template,
} from "./documents-api";
import { Thumbnail } from "./template-panel";
import {
  COLUMNS_OPTIONS,
  descriptionOf,
  documentPath,
  galleryLanguage,
  isFiltered,
  matches,
  nameOf,
  NO_FILTERS,
  ordered,
  PHOTO_OPTIONS,
  thumbnailOf,
  useTemplateSwitch,
  type ColumnsFilter,
  type Filters,
  type PhotoFilter,
} from "./template-choice";

/**
 * /documents/templates: every template the api serves, the person's own under "From your CV"
 * first, with names, descriptions and tags, filters (photo, columns, ats friendly), and the letter
 * on hover or tap. Opened from a document (`?document=<id>`), each offers "Use for this document",
 * which switches that document (a base CV's template is what the CVs written from it afterwards
 * take).
 */
export function TemplatesPage() {
  useTitle("Templates");
  const [params] = useSearchParams();
  const documentId = params.get("document") ?? "";
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const templates = useQuery({
    queryKey: templatesKey,
    queryFn: ({ signal }) => listTemplates(signal),
  });
  const target = useQuery({
    queryKey: documentKey(documentId),
    queryFn: ({ signal }) => getDocument(documentId, signal),
    enabled: documentId !== "",
  });
  const document = target.data;
  const language = galleryLanguage(document?.language);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      {document ? (
        <Button asChild variant="ghost" size="sm" className="w-fit">
          <Link to={documentPath(document)}>
            <ArrowLeft aria-hidden="true" />
            Back to the document
          </Link>
        </Button>
      ) : null}
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Templates</h1>
        <p className="text-sm text-muted-foreground">
          {document
            ? `Choose the look of ${documentName(document)}. The words stay the same.`
            : "The looks a CV and its letter can take. Open a document and choose Template to use one."}
        </p>
      </header>
      {documentId !== "" && target.isError ? (
        <Alert>
          <AlertDescription>
            The document this gallery was opened from could not be loaded, so no
            template can be used from here. {describeError(target.error)}
          </AlertDescription>
        </Alert>
      ) : null}
      <FilterBar filters={filters} onChange={setFilters} />
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
          <AlertDescription className="flex flex-col gap-3">
            <p>{describeError(templates.error)}</p>
            <Button
              variant="outline"
              size="sm"
              className="w-fit"
              onClick={() => void templates.refetch()}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
        <Gallery
          templates={ordered(templates.data)}
          filters={filters}
          language={language}
          document={document}
          onClear={() => {
            setFilters(NO_FILTERS);
          }}
        />
      )}
    </div>
  );
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
    <fieldset className="flex flex-wrap items-end gap-4">
      <legend className="sr-only">Filters</legend>
      <div className="flex w-40 flex-col gap-1">
        <Label htmlFor={photoId}>Photo</Label>
        <NativeSelect
          id={photoId}
          value={filters.photo}
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
      </div>
      <div className="flex w-40 flex-col gap-1">
        <Label htmlFor={columnsId}>Columns</Label>
        <NativeSelect
          id={columnsId}
          value={filters.columns}
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
      </div>
      <div className="flex h-9 items-center gap-2">
        <input
          id={atsId}
          type="checkbox"
          className="size-4 accent-primary"
          checked={filters.ats}
          onChange={(event) => {
            onChange({ ...filters, ats: event.target.checked });
          }}
        />
        <Label htmlFor={atsId}>ATS friendly only</Label>
      </div>
    </fieldset>
  );
}

function Gallery({
  templates,
  filters,
  language,
  document,
  onClear,
}: {
  templates: Template[];
  filters: Filters;
  language: Language;
  document: DocumentOut | undefined;
  onClear: () => void;
}) {
  const choose = useTemplateSwitch(document);
  const shown = templates.filter((one) => matches(one, filters));
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
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {isFiltered(filters)
          ? `${String(shown.length)} of ${String(templates.length)} templates match.`
          : `${String(templates.length)} templates.`}
      </p>
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
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm">No template matches these filters.</p>
          <Button variant="outline" size="sm" onClick={onClear}>
            Clear the filters
          </Button>
        </div>
      ) : null}
      {own.length > 0 ? (
        <section
          aria-labelledby="own-templates"
          className="flex flex-col gap-3"
        >
          <h2 id="own-templates" className="text-lg font-semibold">
            From your CV
          </h2>
          <Grid>{own.map(card)}</Grid>
        </section>
      ) : null}
      {bundled.length > 0 ? (
        <section
          aria-labelledby="all-templates"
          className="flex flex-col gap-3"
        >
          <h2 id="all-templates" className="text-lg font-semibold">
            All templates
          </h2>
          <Grid>{bundled.map(card)}</Grid>
        </section>
      ) : null}
    </div>
  );
}

function Grid({ children }: { children: ReactNode }) {
  return (
    <ul
      className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4"
      data-testid="template-grid"
    >
      {children}
    </ul>
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

  return (
    <li
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-2 rounded-lg border p-2"
      data-testid={`template-${template.slug}`}
    >
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
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={headingId} className="font-semibold">
          {name}
        </h3>
        {inUse ? (
          <Badge variant="secondary">
            <Check aria-hidden="true" />
            In use
          </Badge>
        ) : null}
      </div>
      {template.user ? (
        <p className="text-xs text-muted-foreground">From your CV</p>
      ) : null}
      {description ? (
        <p className="text-sm text-muted-foreground">{description}</p>
      ) : null}
      {template.tags.length > 0 ? (
        <ul aria-label={`Tags of ${name}`} className="flex flex-wrap gap-1">
          {template.tags.map((tag) => (
            <li key={tag}>
              <Badge variant="outline">{tag}</Badge>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-auto flex flex-wrap gap-2">
        {hasLetter ? (
          <Button
            size="sm"
            variant="ghost"
            aria-pressed={pinned}
            onClick={() => {
              setPinned((value) => !value);
            }}
          >
            {pinned ? "See the CV" : "See the letter"}
          </Button>
        ) : null}
        {document && !inUse ? (
          <Button size="sm" disabled={busy} onClick={onUse}>
            Use for this document
          </Button>
        ) : null}
      </div>
    </li>
  );
}
