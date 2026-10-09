import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CircleAlert,
  Download,
  FileJson,
  LoaderCircle,
  Upload,
} from "lucide-react";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import {
  conflictOf,
  exportProfile,
  importProfile,
  profileKey,
  type Profile,
  type SectionName,
} from "./api";
import { downloadJson } from "./download";
import {
  checkImportFile,
  exportFileName,
  formatBytes,
  type ImportCheck,
} from "./import-file";
import { SECTION_LABELS } from "./labels";
import { absoluteTime } from "./when";

/**
 * The page's "Export or import" menu. Export downloads GET /api/profile/export as
 * profile-YYYY-MM-DD.json; Import opens a dialog that checks a file in the browser, says what it
 * would replace, and posts it with the page's revision.
 */
export function ProfileFileMenu({
  profile,
  dirtySection,
  onImported,
}: {
  profile: Profile;
  /** the tab with unsaved changes, if any: an import throws them away */
  dirtySection: SectionName | undefined;
  onImported: () => void;
}) {
  const [importing, setImporting] = useState(false);
  const exporting = useMutation({
    mutationFn: exportProfile,
    onSuccess: (file) => {
      const name = exportFileName(file.exported_at);
      downloadJson(file, name);
      toast.success(`Profile exported as ${name}.`);
    },
    onError: (error) => {
      toast.error(`The profile was not exported. ${describeError(error)}`);
    },
  });
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            disabled={exporting.isPending}
            aria-busy={exporting.isPending}
          >
            {exporting.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : (
              <FileJson aria-hidden="true" />
            )}
            Export or import
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => {
              exporting.mutate();
            }}
          >
            <Download aria-hidden="true" />
            Export profile
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setImporting(true);
            }}
          >
            <Upload aria-hidden="true" />
            Import profile
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ImportDialog
        open={importing}
        onOpenChange={setImporting}
        profile={profile}
        dirtySection={dirtySection}
        onImported={onImported}
      />
    </>
  );
}

interface ChosenFile {
  name: string;
  size: number;
  text: string;
}

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
  const client = useQueryClient();
  const [file, setFile] = useState<ChosenFile>();
  const [reading, setReading] = useState(false);
  const [serverError, setServerError] = useState<string>();
  const [stale, setStale] = useState(false);
  // Recomputed when the profile changes (after a 409), so the summary compares with the latest.
  const check: ImportCheck | undefined = useMemo(
    () => (file ? checkImportFile(file.text, file.size, profile) : undefined),
    [file, profile],
  );

  const mutation = useMutation({
    mutationFn: (document: Record<string, unknown>) =>
      importProfile(document, profile.revision),
    onMutate: () => {
      setServerError(undefined);
    },
    onSuccess: (saved) => {
      client.setQueryData(profileKey, saved);
      void client.invalidateQueries({ queryKey: ["profile", "revisions"] });
      toast.success("Profile imported.");
      onDone();
    },
    onError: (error) => {
      const current = conflictOf(error);
      if (current) {
        client.setQueryData<Profile>(profileKey, (old) =>
          old
            ? { ...old, revision: current.revision, sections: current.sections }
            : old,
        );
        setStale(true);
        return;
      }
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
      <div className="flex flex-col gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          Profile file
        </label>
        <input
          id={id}
          type="file"
          accept="application/json,.json"
          disabled={mutation.isPending}
          className="block w-full min-w-0 text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium"
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
      </div>
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
            <LoaderCircle aria-hidden="true" className="animate-spin" />
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
