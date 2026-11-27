import { EllipsisVertical, Play } from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import type { SavedSearch } from "@/features/jobs/api/searches-api";
import { formatDateTime } from "@/features/jobs/helpers/format";
import {
  NAME_MAX,
  criteriaLine,
  nameProblem,
  scheduleWords,
} from "@/features/jobs/helpers/saved-searches";
import { plural } from "@/features/jobs/helpers/selection";

/** Where a search stands: running, waiting in the line, or its last run, however old, whose new
 * postings open in the Jobs list. */
function StateLine({
  search,
  running,
  onOpenPostings,
}: {
  search: SavedSearch;
  running: boolean;
  onOpenPostings: () => void;
}): ReactNode {
  const last = search.last_run;
  if (running || last?.status === "running") return "Running now.";
  if (search.request)
    return `Waits in line: number ${String(search.request.place)}.`;
  if (!last) return "Not run yet.";
  const when = `Last run ${formatDateTime(last.started_at)}`;
  if (last.status === "failed") return `${when}, it stopped.`;
  if (!last.new) return `${when}, no new posting.`;
  return (
    <>
      {when},{" "}
      <Link
        to={`/jobs?sweep=${encodeURIComponent(last.sweep_id)}`}
        onClick={onOpenPostings}
        className="font-medium text-foreground underline underline-offset-4"
      >
        {plural(last.new, "new posting")}
      </Link>
      .
    </>
  );
}

/**
 * One search of Your searches: its name, one line of what it searches, its schedule and where it
 * stands, with Run now and Edit, and Schedule, Duplicate, Rename and Delete in its menu (the main
 * search is neither renamed nor deleted). A run waiting in the line says its place and can be
 * taken back.
 */
export function SearchRow({
  search,
  running,
  names,
  disabled,
  onRun,
  onEdit,
  onSchedule,
  onDuplicate,
  onRename,
  onDelete,
  onTakeBack,
  onOpenPostings,
}: {
  search: SavedSearch;
  running: boolean;
  /** The names of the other saved searches. */
  names: readonly string[];
  disabled: boolean;
  onRun: () => void;
  onEdit: () => void;
  onSchedule: () => void;
  onDuplicate: () => void;
  /** Resolves with what went wrong, or null once renamed. */
  onRename: (name: string) => Promise<string | null>;
  onDelete: () => void;
  onTakeBack: () => void;
  /** Its new postings open in the Jobs list: Find jobs closes. */
  onOpenPostings: () => void;
}) {
  const id = useId();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(search.name);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const rename = async (event: FormEvent) => {
    event.preventDefault();
    const wrong = nameProblem(name, names);
    if (wrong) {
      setProblem(wrong);
      return;
    }
    setSaving(true);
    const failed = await onRename(name.trim());
    setSaving(false);
    setProblem(failed);
    if (!failed) setRenaming(false);
  };

  return (
    <li className="flex flex-col gap-2 py-3" aria-labelledby={`${id}-name`}>
      <div className="flex items-start gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          {renaming ? (
            <form
              onSubmit={(event) => void rename(event)}
              className="flex flex-wrap items-center gap-2"
            >
              <Input
                aria-label="New name"
                aria-invalid={problem ? true : undefined}
                aria-describedby={problem ? `${id}-problem` : undefined}
                value={name}
                maxLength={NAME_MAX}
                autoFocus
                disabled={saving}
                className="h-8 min-w-0 flex-1"
                onChange={(event) => {
                  setName(event.target.value);
                  setProblem(null);
                }}
              />
              <Button type="submit" size="sm" disabled={saving}>
                Save name
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={saving}
                onClick={() => {
                  setRenaming(false);
                  setName(search.name);
                  setProblem(null);
                }}
              >
                Cancel
              </Button>
            </form>
          ) : null}
          <h3
            id={`${id}-name`}
            className={renaming ? "sr-only" : "font-medium break-words"}
          >
            {search.name}
          </h3>
          {problem ? (
            <p
              id={`${id}-problem`}
              role="alert"
              className="text-sm text-destructive"
            >
              {problem}
            </p>
          ) : null}
          <p className="line-clamp-2 text-sm break-words text-muted-foreground">
            {criteriaLine(search.criteria)}
          </p>
          <p className="text-xs text-muted-foreground">
            {scheduleWords(search.schedule)}.{" "}
            <StateLine
              search={search}
              running={running}
              onOpenPostings={onOpenPostings}
            />
          </p>
        </div>
        <Button
          size="sm"
          disabled={disabled || running || search.request !== null}
          onClick={onRun}
          aria-label={`Run ${search.name} now`}
        >
          <Play aria-hidden="true" />
          Run now
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={disabled}
          onClick={onEdit}
          aria-label={`Edit ${search.name}`}
        >
          Edit
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon"
              variant="ghost"
              className="size-8"
              disabled={disabled}
              aria-label={`More actions for ${search.name}`}
            >
              <EllipsisVertical aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onSchedule}>Schedule</DropdownMenuItem>
            <DropdownMenuItem onSelect={onDuplicate}>
              Duplicate
            </DropdownMenuItem>
            {search.main ? null : (
              <>
                <DropdownMenuItem
                  onSelect={() => {
                    setRenaming(true);
                  }}
                >
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => {
                    setDeleting(true);
                  }}
                >
                  Delete
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {search.request ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Spinner aria-hidden="true" role={undefined} />
          <span>
            {search.request.place === 1
              ? "Starts when the running search ends."
              : `Starts after ${plural(search.request.place - 1, "other search", "other searches")}.`}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={onTakeBack}
          >
            Take back
          </Button>
        </div>
      ) : null}
      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {search.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Its schedule stops and a run waiting in the line is taken back.
              The postings it found stay in your list, and its past searches
              keep its name. The permissions you gave to job websites stay.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <Button
              variant="destructive"
              onClick={() => {
                setDeleting(false);
                onDelete();
              }}
            >
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}
