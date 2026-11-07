import { Check, RotateCw } from "lucide-react";
import { useId, useMemo, type ReactNode } from "react";
import { toast } from "sonner";

import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { useProfile } from "@/features/profile/api/use-profile";

import type { PanelProps } from "./document-view";
import {
  failureSentence,
  LANGUAGES,
  type DocumentOut,
  type Language,
} from "@/features/documents/api/documents-api";
import {
  useDocument,
  useDocumentLanguage,
} from "@/features/documents/api/use-document";
import { useTemplates } from "@/features/documents/api/use-templates";
import { useVersions } from "@/features/documents/api/use-version-history";
import {
  useAutosave,
  type SaveStatus,
  type Saver,
} from "@/features/documents/api/use-edit-autosave";
import {
  differences,
  fieldKeys,
  MAX,
  notesByField,
  withDefaults,
  type Draft,
} from "@/features/documents/helpers/edit-content";
import type { Form } from "@/features/documents/helpers/edit-form";
import {
  pageFacts,
  profilePhoto,
} from "@/features/documents/helpers/page-facts";
import { draftStep } from "@/features/documents/helpers/template-choice";
import { DocumentSheet } from "./document-sheet";
import { TextField } from "./edit-fields";
import { FixedList } from "./edit-forms/fixed-list";
import { FormSection } from "./edit-forms/form-section";
import { ProfileFacts } from "./edit-forms/profile-facts";
import { FIXED_LISTS } from "@/features/documents/helpers/fixed-facts";
import { fieldLabel, isOpen } from "@/features/documents/helpers/labels";

const LANGUAGE_NAMES: Record<Language, string> = {
  en: "English",
  fr: "French",
};

/** What the section of the page says above it: the version it is, and its marks. */
export interface PageHeading {
  title: string;
  meta?: ReactNode;
  /** The overflow note, above the page. */
  fit?: ReactNode;
  /** Right under the page: what the fact check corrected in it. */
  below?: ReactNode;
}

/**
 * The draft of a document as its page, edited where it is read (SPEC-documents.md, "Pages"):
 * a click on a text of the page puts its field in its place (in-place.tsx), and every change is
 * saved by itself. Under it, what is not printed (what the document is for, the notes) and where
 * the printed facts come from.
 */
export function EditPanel(props: PanelProps & { heading: PageHeading }) {
  // The saver is bound to one document: another document gets a form of its own.
  return <EditForm key={props.document.id} {...props} />;
}

function EditForm({
  document,
  generation,
  heading,
}: PanelProps & { heading: PageHeading }) {
  const id = useId();
  const { draft, status, saved, round, saver } = useAutosave(document);
  const profile = useProfile();
  const templates = useTemplates({ staleTime: 5 * 60_000 });
  const baseId =
    document.scope === "posting" ? document.base_document_id : null;
  const base = useDocument(baseId ?? "", {
    enabled: baseId !== null && document.kind === "cv",
  });
  const versions = useVersions(document);
  const language = useDocumentLanguage(document.id, {
    onError: (error) => {
      toast.error(failureSentence(error));
    },
  });

  const letter = document.kind === "letter";
  const notes = useMemo(() => notesByField(saved, draft), [saved, draft]);
  const form: Form = {
    id,
    draft,
    set: (path, value) => {
      saver.edit(path, value);
    },
    notes,
    errors:
      status.state === "failed" ? status.fields : new Map<string, string>(),
    limits:
      templates.data?.find((one) => one.slug === document.template_slug)
        ?.soft_limits?.[letter ? "letter" : "cv"] ?? undefined,
    round,
  };
  const known =
    profile.isSuccess && (baseId === null || letter || base.isSuccess);
  const facts = pageFacts(
    profile.data?.sections,
    document.scope === "base" ? draft : (base.data?.draft ?? {}),
  );
  const approved = versions.data?.find((one) => one.approved_at !== null);
  const general = notes.get("") ?? [];
  const noun = letter ? "letter" : "CV";

  return (
    <div
      className="flex flex-col gap-8"
      data-testid="edit-panel"
      onBlur={() => {
        saver.flush();
      }}
    >
      <Section
        title={heading.title}
        description={heading.meta}
        actions={
          <Field className="gap-1.5 sm:w-48">
            <FieldLabel htmlFor={`${id}-language`}>
              Language of the page
            </FieldLabel>
            <NativeSelect
              id={`${id}-language`}
              value={document.language}
              disabled={language.isPending}
              onChange={(event) => {
                language.mutate(event.target.value as Language);
              }}
            >
              {LANGUAGES.map((one) => (
                <option key={one} value={one}>
                  {LANGUAGE_NAMES[one]}
                </option>
              ))}
            </NativeSelect>
          </Field>
        }
      >
        <div className="flex flex-col gap-4">
          {heading.fit}
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <SaveLine
              status={status}
              shown={fieldKeys(draft, letter, document.scope === "base")}
              document={document}
              saver={saver}
            />
            {status.state === "failed" ? null : (
              <p className="text-sm text-muted-foreground">
                Click a text of the page to change it.
              </p>
            )}
          </div>
          {document.language === "fr" ? (
            <p className="text-sm text-muted-foreground">
              The headings and the date print in French; your text prints as you
              write it.
            </p>
          ) : null}
          {isOpen(generation) ? (
            <p className="text-sm text-muted-foreground">
              The writer is working on this document. If it saves a new draft
              while you type, you choose which text to keep.
            </p>
          ) : null}
          {approved === undefined ? null : (
            <p className="text-sm text-muted-foreground">
              Version {approved.number} is approved. What you change here makes
              a new version, which you approve under Versions once its page is
              drawn.
            </p>
          )}
          {general.length === 0 ? null : (
            <ul className="text-sm text-warning">
              {general.map((note, index) => (
                <li key={index}>{note.sentence}</li>
              ))}
            </ul>
          )}
          <DocumentSheet
            kind={document.kind}
            language={document.language}
            template={document.template_slug}
            step={draftStep(versions.data, document.template_slug)}
            photo={profilePhoto(profile.data)}
            content={draft}
            facts={facts}
            form={form}
            known={known}
          />
        </div>
      </Section>
      {heading.below}
      <Section
        title="Not printed"
        description={
          letter
            ? "What this letter is for stays here; your name and contact lines print from your profile."
            : "What this CV is for and your notes stay here; your name, contact lines, employers, schools and languages print from your profile."
        }
      >
        <FormSection title="For you">
          <TextField
            form={form}
            path={["target"]}
            label={`What this ${noun} is for (not printed)`}
            max={MAX.line}
          />
          {letter ? null : (
            <TextField
              form={form}
              path={["notes"]}
              label="Notes (not printed)"
              max={MAX.notes}
              multiline
            />
          )}
        </FormSection>
        <ProfileFacts profile={profile.data?.sections} letter={letter} />
        {document.scope === "base" && !letter ? (
          <FormSection title="Facts of this base CV">
            <p className="text-sm text-muted-foreground">
              Used only where your profile is still empty: your profile always
              comes first.
            </p>
            {FIXED_LISTS.map((list) => (
              <FixedList key={list.name} form={form} list={list} />
            ))}
          </FormSection>
        ) : null}
      </Section>
      <Conflict status={status} draft={draft} saver={saver} />
    </div>
  );
}

/** A refusal at a key with no input of the form (`shown`) is said here, else under its field. */
function SaveLine({
  status,
  shown,
  document,
  saver,
}: {
  status: SaveStatus;
  shown: Set<string>;
  document: DocumentOut;
  saver: Saver;
}) {
  if (status.state === "failed") {
    const refused = [...status.fields];
    const below = refused.some(([key]) => shown.has(key));
    const here = refused
      .filter(([key]) => !shown.has(key))
      .map(([key, sentence]) => ({
        key,
        line:
          key === ""
            ? sentence
            : `${fieldLabel(key).replace(/_/g, " ")}: ${sentence}`,
      }));
    return (
      <Alert variant="destructive" className="w-full">
        <AlertTitle>Not saved</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          {refused.length === 0 ? <span>{status.message}</span> : null}
          {below ? <span>A field below cannot be saved as it is.</span> : null}
          {here.length === 0 ? null : (
            <ul className="flex flex-col gap-1">
              {here.map((one) => (
                <li key={one.key}>{one.line}</li>
              ))}
            </ul>
          )}
          {refused.length === 0 ? null : <span>Your text stays here.</span>}
          <Button variant="outline" size="sm" onClick={() => void saver.save()}>
            <RotateCw aria-hidden="true" />
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  const drawing = document.render.state !== "idle";
  const line =
    status.state === "saving"
      ? "Saving"
      : status.state === "waiting" || status.state === "conflict"
        ? "Not saved yet"
        : drawing
          ? "Saved, the new page is being drawn"
          : "Saved";
  return (
    <p
      role="status"
      className="flex items-center gap-2 text-sm text-muted-foreground"
    >
      {status.state === "saving" || (status.state === "saved" && drawing) ? (
        <Spinner aria-hidden="true" role={undefined} />
      ) : status.state === "saved" ? (
        <Check aria-hidden="true" className="size-4" />
      ) : null}
      {line}
    </p>
  );
}

/** A 409: both texts, field by field, and the person keeps one. */
function Conflict({
  status,
  draft,
  saver,
}: {
  status: SaveStatus;
  draft: Draft;
  saver: Saver;
}) {
  const theirs = status.state === "conflict" ? status.theirs : undefined;
  const changed =
    theirs === undefined ? [] : differences(draft, withDefaults(theirs));
  return (
    <AlertDialog open={theirs !== undefined}>
      <AlertDialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle>
            This document changed in another tab
          </AlertDialogTitle>
          <AlertDialogDescription>
            It was saved somewhere else while you were typing here. Keep one of
            the two texts; the other is replaced.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {changed.length === 0 ? (
          <p className="text-sm">The two texts are the same.</p>
        ) : (
          <ul className="flex flex-col gap-3" aria-label="What differs">
            {changed.map((one, index) => (
              <li key={index} className="flex flex-col gap-1 text-sm">
                <p className="font-medium">{one.label}</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="rounded-md border p-2">
                    <p className="text-xs text-muted-foreground">Yours</p>
                    <p className="whitespace-pre-wrap">{one.mine || "Empty"}</p>
                  </div>
                  <div className="rounded-md border p-2">
                    <p className="text-xs text-muted-foreground">
                      In the other tab
                    </p>
                    <p className="whitespace-pre-wrap">
                      {one.theirs || "Empty"}
                    </p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        <AlertDialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              if (theirs) saver.takeTheirs(theirs);
            }}
          >
            Use the other tab&apos;s text
          </Button>
          <Button
            onClick={() => {
              if (theirs) saver.keepMine(theirs);
            }}
          >
            Keep my text
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
