import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, RotateCw } from "lucide-react";
import { useId, useMemo } from "react";
import { toast } from "sonner";

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
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { getProfile, profileKey } from "@/features/profile/api";

import type { PanelProps } from "./document-view";
import {
  documentKey,
  failureSentence,
  getDocument,
  LANGUAGES,
  listTemplates,
  listVersions,
  patchDocument,
  templatesKey,
  versionsKey,
  type DocumentOut,
  type Language,
} from "./documents-api";
import { useAutosave, type SaveStatus, type Saver } from "./edit-autosave";
import {
  differences,
  listAt,
  notesByField,
  withDefaults,
  type Draft,
  type EmployerFact,
} from "./edit-content";
import type { Form } from "./edit-lists";
import { CvForm, LetterForm, ProfileFacts } from "./edit-forms";
import { isOpen } from "./labels";

/** The Edit view: the structured form of the draft, saved by itself (SPEC-documents.md, "Pages"). */
export const available = true;

const LANGUAGE_NAMES: Record<Language, string> = {
  en: "English",
  fr: "French",
};

const text = (value: unknown): string =>
  typeof value === "string" ? value : "";

/** The profile's employers, then those only the base CV's `fixed` block states (profile_view). */
function employersOf(
  profile: {
    key: string;
    organisation: string;
    title: string;
    start: string;
    end: string | null;
  }[],
  fixed: unknown[],
): Map<string, EmployerFact> {
  const out = new Map<string, EmployerFact>();
  for (const one of profile) out.set(one.key, { ...one });
  for (const raw of fixed) {
    const one = (raw ?? {}) as Record<string, unknown>;
    const key = text(one.key);
    if (key === "" || out.has(key)) continue;
    out.set(key, {
      key,
      organisation: text(one.organisation) || key,
      title: text(one.title),
      start: text(one.start) || null,
      end: text(one.end) || null,
    });
  }
  return out;
}

export function EditPanel({ document, generation }: PanelProps) {
  const id = useId();
  const queryClient = useQueryClient();
  const { draft, status, saved, round, saver } = useAutosave(document);
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
  });
  const templates = useQuery({
    queryKey: templatesKey,
    queryFn: ({ signal }) => listTemplates(signal),
    staleTime: 5 * 60_000,
  });
  const baseId =
    document.scope === "posting" ? document.base_document_id : null;
  const base = useQuery({
    queryKey: documentKey(baseId ?? ""),
    queryFn: ({ signal }) => getDocument(baseId ?? "", signal),
    enabled: baseId !== null && document.kind === "cv",
  });
  const versions = useQuery({
    queryKey: [...versionsKey(document.id), document.latest?.number ?? 0],
    queryFn: ({ signal }) => listVersions(document.id, signal),
  });
  const language = useMutation({
    mutationFn: (value: Language) =>
      patchDocument(document.id, { language: value }),
    onSuccess: (answer) => {
      queryClient.setQueryData(documentKey(document.id), answer);
    },
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
  };
  const fixed =
    document.scope === "base"
      ? listAt(draft, ["fixed", "employers"])
      : listAt(base.data?.draft ?? {}, ["fixed", "employers"]);
  const known =
    profile.isSuccess && (baseId === null || letter || base.isSuccess);
  const employers = employersOf(
    profile.data?.sections.experience.employers ?? [],
    fixed,
  );
  const approved = versions.data?.find((one) => one.approved_at !== null);
  const general = notes.get("") ?? [];

  return (
    <div className="flex flex-col gap-4" data-testid="edit-panel">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <SaveLine status={status} document={document} saver={saver} />
        <div className="flex w-full flex-col gap-1.5 sm:w-48">
          <Label htmlFor={`${id}-language`}>Language of the page</Label>
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
        </div>
      </div>
      {document.language === "fr" ? (
        <p className="text-sm text-muted-foreground">
          The headings and the date print in French; your text prints as you
          write it.
        </p>
      ) : null}
      {isOpen(generation) ? (
        <p className="text-sm text-muted-foreground">
          The writer is working on this document. If it saves a new draft while
          you type, you choose which text to keep.
        </p>
      ) : null}
      {approved === undefined ? null : (
        <p className="text-sm text-muted-foreground">
          Version {approved.number} is approved. What you change here makes a
          new version, which you approve in See once its page is drawn.
        </p>
      )}
      {general.length === 0 ? null : (
        <ul className="text-sm text-amber-700 dark:text-amber-400">
          {general.map((note, index) => (
            <li key={index}>{note.sentence}</li>
          ))}
        </ul>
      )}
      <ProfileFacts profile={profile.data} letter={letter} />
      <div
        key={round}
        className="flex flex-col gap-4"
        onBlur={() => {
          saver.flush();
        }}
      >
        {letter ? (
          <LetterForm form={form} />
        ) : (
          <CvForm
            form={form}
            base={document.scope === "base"}
            employers={employers}
            known={known}
          />
        )}
      </div>
      <Conflict status={status} draft={draft} saver={saver} />
    </div>
  );
}

function SaveLine({
  status,
  document,
  saver,
}: {
  status: SaveStatus;
  document: DocumentOut;
  saver: Saver;
}) {
  if (status.state === "failed") {
    return (
      <Alert variant="destructive" className="w-full">
        <AlertTitle>Not saved</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <span>
            {status.fields.size > 0
              ? "A field below cannot be saved as it is. Your text stays here."
              : status.message}
          </span>
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
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
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
