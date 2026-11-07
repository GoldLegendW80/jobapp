import { CircleAlert, Upload } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import { type Profile, type SectionName } from "../api/profile-api";
import { useImportProfile } from "../api/use-profile-file";
import {
  checkImportFile,
  formatBytes,
  type ImportCheck,
} from "../helpers/import-file";
import { SECTION_LABELS } from "../helpers/labels";
import { absoluteTime } from "../helpers/when";

interface ChosenFile {
  name: string;
  size: number;
  text: string;
}

/**
 * Import profile, from the profile page's More actions: checks a file in the browser, says what it
 * would replace, and posts it with the page's revision. Export is the menu's own item.
 */
export function ImportDialog({
  open,
  onOpenChange,
  profile,
  dirtySection,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profile: Profile;
  dirtySection: SectionName | undefined;
  onImported: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Import profile</DialogTitle>
          <DialogDescription>
            Replace your whole profile with a file exported from this app. Each
            section becomes a new version in its history, so what you have now
            can be restored.
          </DialogDescription>
        </DialogHeader>
        {open ? (
          <ImportBody
            profile={profile}
            dirtySection={dirtySection}
            onDone={() => {
              onOpenChange(false);
              onImported();
            }}
            onCancel={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function ImportBody({
  profile,
  dirtySection,
  onDone,
  onCancel,
}: {
  profile: Profile;
  dirtySection: SectionName | undefined;
  onDone: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [file, setFile] = useState<ChosenFile>();
  const [reading, setReading] = useState(false);
  const [serverError, setServerError] = useState<string>();
  const [stale, setStale] = useState(false);
  // Recomputed when the profile changes (after a 409), so the summary compares with the latest.
  const check: ImportCheck | undefined = useMemo(
    () => (file ? checkImportFile(file.text, file.size, profile) : undefined),
    [file, profile],
  );

  const mutation = useImportProfile(profile.revision, {
    onStart: () => {
      setServerError(undefined);
    },
    onImported: () => {
      toast.success("Profile imported.");
      onDone();
    },
    onStale: () => {
      setStale(true);
    },
    onRefused: (error) => {
      if (isApiError(error) && error.status === 413) {
        setServerError(
          `The server refused the file: ${errorDetail(error) ?? "it is larger than 2 MB."}`,
        );
        return;
      }
      if (isApiError(error) && error.status === 422) {
        setServerError(
          `The server refused the file: ${errorDetail(error) ?? "it is not a valid profile export."}`,
        );
        return;
      }
      setServerError(`The profile was not imported. ${describeError(error)}`);
    },
  });

  const choose = async (chosen: File | undefined) => {
    setServerError(undefined);
    setStale(false);
    if (!chosen) {
      setFile(undefined);
      return;
    }
    setReading(true);
    try {
      // Read at most a little more than the limit: the check reports the real size anyway.
      const text = chosen.size > 3 * 1024 * 1024 ? "" : await chosen.text();
      setFile({ name: chosen.name, size: chosen.size, text });
    } catch {
      setFile(undefined);
      setServerError("The file could not be read.");
    } finally {
      setReading(false);
    }
  };

  const summary = check?.ok ? check.summary : undefined;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Field className="gap-2">
        <FieldLabel htmlFor={id}>Profile file</FieldLabel>
        <Input
          id={id}
          type="file"
          accept="application/json,.json"
          disabled={mutation.isPending}
          className="file:mr-3"
          onChange={(event) => {
            const chosen = event.target.files?.[0];
            // Cleared at once, so choosing the same file again (after fixing it) fires a change.
            event.target.value = "";
            void choose(chosen);
          }}
        />
        {file ? (
          <p className="text-xs break-all text-muted-foreground">
            {file.name}, {formatBytes(file.size)}
          </p>
        ) : null}
        {reading ? (
          <p role="status" className="text-sm text-muted-foreground">
            Reading the file
          </p>
        ) : null}
      </Field>
      {check && !check.ok ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>This file cannot be imported</AlertTitle>
          <AlertDescription>{check.error}</AlertDescription>
        </Alert>
      ) : null}
      {summary ? (
        <section
          aria-label="What the import replaces"
          className="flex flex-col gap-3"
        >
          {summary.exportedAt ? (
            <p className="text-sm text-muted-foreground">
              Exported {absoluteTime(summary.exportedAt)}.
            </p>
          ) : null}
          <ul className="flex flex-col divide-y rounded-md border text-sm">
            {summary.sections.map((section) => (
              <li
                key={section.name}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2"
              >
                <span className="min-w-0">
                  <span className="font-medium">{section.label}</span>
                  <span className="text-muted-foreground">
                    : {section.detail}
                  </span>
                </span>
                <Badge variant={section.changed ? "default" : "secondary"}>
                  {section.changed ? "Replaced" : "Unchanged"}
                </Badge>
              </li>
            ))}
            <li className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2">
              <span>
                <span className="font-medium">Photo</span>
                <span className="text-muted-foreground">
                  :{" "}
                  {summary.photo
                    ? "the file has one"
                    : profile.has_photo
                      ? "none in the file, yours is kept"
                      : "none in the file"}
                </span>
              </span>
              <Badge variant={summary.photo ? "default" : "secondary"}>
                {summary.photo ? "Replaced" : "Kept"}
              </Badge>
            </li>
          </ul>
          <p className="text-sm">
            Completeness after the import:{" "}
            <span className="font-medium tabular-nums">
              {summary.completeness.overall}%
            </span>{" "}
            <span className="text-muted-foreground">
              (now {profile.completeness.overall}%)
            </span>
          </p>
          {dirtySection ? (
            <Alert>
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                Your unsaved changes to {SECTION_LABELS[dirtySection]} will be
                lost.
              </AlertDescription>
            </Alert>
          ) : null}
        </section>
      ) : null}
      {stale ? (
        <Alert>
          <CircleAlert aria-hidden="true" />
          <AlertTitle>Your profile changed elsewhere</AlertTitle>
          <AlertDescription>
            It was saved from another tab or device after you opened this page.
            The summary now compares the file with that version. Import anyway
            to replace it.
          </AlertDescription>
        </Alert>
      ) : null}
      {serverError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      ) : null}
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          disabled={!check?.ok || mutation.isPending}
          aria-busy={mutation.isPending}
          onClick={() => {
            if (check?.ok) mutation.mutate(check.document);
          }}
        >
          {mutation.isPending ? (
            <Spinner aria-hidden="true" />
          ) : (
            <Upload aria-hidden="true" />
          )}
          {mutation.isPending
            ? "Importing"
            : stale
              ? "Import anyway"
              : "Replace my profile"}
        </Button>
      </DialogFooter>
    </div>
  );
}
