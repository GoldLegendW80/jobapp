import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowLeft,
  History,
  LoaderCircle,
  Minus,
  Plus,
  RotateCcw,
  RotateCw,
} from "lucide-react";
import { useId, useState } from "react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  conflictOf,
  getRevision,
  listRevisions,
  profileKey,
  revisionKey,
  revisionsKey,
  saveSection,
  type Profile,
  type Revision,
  type SectionName,
} from "./api";
import {
  diffFields,
  diffLines,
  factsText,
  flattenSection,
  sameData,
  type FieldDiff,
} from "./diff";
import { SECTION_LABELS } from "./labels";
import { absoluteTime, relativeTime } from "./when";

/** Put a 409's current state in the page cache, as useSectionForm does. */
function applyConflict(
  client: ReturnType<typeof useQueryClient>,
  error: unknown,
): boolean {
  const current = conflictOf(error);
  if (!current) return false;
  client.setQueryData<Profile>(profileKey, (old) =>
    old
      ? { ...old, revision: current.revision, sections: current.sections }
      : old,
  );
  return true;
}

/**
 * The History button of a tab and its drawer: the section's saves, newest first, 20 at a time;
 * choosing one compares it with the current version, and "Restore this version" saves its data
 * as a new revision (PUT with the page's revision, so a save made elsewhere meanwhile is a 409,
 * never overwritten silently). With unsaved changes in the tab, restoring asks first.
 * `onRestored` lets the page reload the tab's form from the new current version.
 */
export function HistoryDrawer({
  section,
  profile,
  dirty,
  onRestored,
}: {
  section: SectionName;
  profile: Profile;
  dirty: boolean;
  onRestored: () => void;
}) {
  const [open, setOpen] = useState(false);
  const label = SECTION_LABELS[section];
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={`History of ${label.toLowerCase()}`}
        >
          <History aria-hidden="true" />
          History
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="w-full gap-0 p-0 sm:max-w-3xl"
        aria-describedby={undefined}
      >
        <SheetHeader className="border-b">
          <SheetTitle>{label} history</SheetTitle>
          <SheetDescription>
            Every save of {label.toLowerCase()} is kept. Choose one to compare
            it with the current version and restore it.
          </SheetDescription>
        </SheetHeader>
        {open ? (
          <HistoryBody
            section={section}
            profile={profile}
            dirty={dirty}
            onRestored={onRestored}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function HistoryBody({
  section,
  profile,
  dirty,
  onRestored,
}: {
  section: SectionName;
  profile: Profile;
  dirty: boolean;
  onRestored: () => void;
}) {
  const client = useQueryClient();
  const label = SECTION_LABELS[section];
  const [selected, setSelected] = useState<number>();
  const versions = useInfiniteQuery({
    queryKey: revisionsKey(section),
    queryFn: ({ pageParam, signal }) =>
      listRevisions(section, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
    // Saves happen in the tab next to it: always read the list afresh when the drawer opens.
    staleTime: 0,
  });
  const items = versions.data?.pages.flatMap((page) => page.items) ?? [];
  const newest = items[0]?.revision;
  const moreError = versions.isFetchNextPageError ? versions.error : null;

  return (
    <div className="grid min-h-0 flex-1 md:grid-cols-[14rem_1fr]">
      <nav
        aria-label={`Versions of ${label.toLowerCase()}`}
        className={cn(
          "min-h-0 overflow-y-auto border-b p-2 md:border-r md:border-b-0",
          selected !== undefined && "hidden md:block",
        )}
      >
        {versions.isPending ? (
          <p
            role="status"
            className="flex items-center gap-2 p-2 text-sm text-muted-foreground"
          >
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Loading versions
          </p>
        ) : versions.isError ? (
          <div className="flex flex-col gap-2 p-2">
            <p role="alert" className="text-sm text-destructive">
              The versions could not be loaded. {describeError(versions.error)}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void versions.refetch()}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </div>
        ) : items.length === 0 ? (
          <p className="p-2 text-sm text-muted-foreground">
            No saved version of {label.toLowerCase()} yet. Each save adds one
            here.
          </p>
        ) : (
          <>
            <ol className="flex flex-col gap-1">
              {items.map((item) => (
                <li key={item.revision}>
                  <button
                    type="button"
                    aria-current={
                      selected === item.revision ? "true" : undefined
                    }
                    aria-label={`Revision ${String(item.revision)}${item.revision === newest ? ", current" : ""}, saved ${relativeTime(item.created_at)}, ${absoluteTime(item.created_at)}`}
                    onClick={() => {
                      setSelected(item.revision);
                    }}
                    className={cn(
                      "flex w-full flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                      selected === item.revision && "bg-accent",
                    )}
                  >
                    <span className="flex items-center gap-2 font-medium">
                      Revision {item.revision}
                      {item.revision === newest ? (
                        <Badge variant="secondary">Current</Badge>
                      ) : null}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      <time
                        dateTime={item.created_at}
                        title={absoluteTime(item.created_at)}
                      >
                        {relativeTime(item.created_at)}
                      </time>
                      <span aria-hidden="true">, </span>
                      <span className="sr-only">, </span>
                      {absoluteTime(item.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
            {versions.hasNextPage ? (
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 w-full"
                disabled={versions.isFetchingNextPage}
                onClick={() => void versions.fetchNextPage()}
              >
                {versions.isFetchingNextPage ? (
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                ) : null}
                {versions.isFetchingNextPage ? "Loading" : "Load more"}
              </Button>
            ) : null}
            {moreError ? (
              <p role="alert" className="mt-2 px-2 text-sm text-destructive">
                More versions could not be loaded. {describeError(moreError)}
              </p>
            ) : null}
          </>
        )}
      </nav>
      <div
        className={cn(
          "min-h-0 overflow-y-auto p-4",
          selected === undefined && "hidden md:block",
        )}
      >
        {selected === undefined ? (
          <p className="text-sm text-muted-foreground">
            Choose a version to compare it with the current one.
          </p>
        ) : (
          <Compare
            key={selected}
            section={section}
            revision={selected}
            isCurrent={selected === newest}
            profile={profile}
            dirty={dirty}
            onBack={() => {
              setSelected(undefined);
            }}
            onConflict={onRestored}
            onRestored={(saved, from) => {
              client.setQueryData(profileKey, saved);
              void client.invalidateQueries({
                queryKey: revisionsKey(section),
              });
              onRestored();
              setSelected(undefined);
              toast.success(
                `Revision ${String(from)} of ${label.toLowerCase()} restored as revision ${String(saved.revision)}.`,
              );
            }}
          />
        )}
      </div>
    </div>
  );
}

function Compare({
  section,
  revision,
  isCurrent,
  profile,
  dirty,
  onBack,
  onRestored,
  onConflict,
}: {
  section: SectionName;
  revision: number;
  isCurrent: boolean;
  profile: Profile;
  dirty: boolean;
  onBack: () => void;
  onRestored: (saved: Profile, from: number) => void;
  /** a 409 put the version saved elsewhere in the cache: the tab must show it */
  onConflict: () => void;
}) {
  const client = useQueryClient();
  const label = SECTION_LABELS[section];
  const [confirming, setConfirming] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string>();
  const version = useQuery({
    queryKey: revisionKey(revision),
    queryFn: ({ signal }) => getRevision(revision, signal),
    // A revision never changes.
    staleTime: Infinity,
  });
  const restore = useMutation({
    mutationFn: (data: Revision["data"]) =>
      saveSection(section, profile.revision, data as never),
    onMutate: () => {
      setError(undefined);
    },
    onSuccess: (saved) => {
      onRestored(saved, revision);
    },
    onError: (failure) => {
      if (applyConflict(client, failure)) {
        // The tab shows the version saved elsewhere; the user decides again.
        onConflict();
        void client.invalidateQueries({ queryKey: revisionsKey(section) });
        setStale(true);
        return;
      }
      const detail =
        isApiError(failure) && failure.status === 422
          ? errorDetail(failure)
          : undefined;
      setError(
        detail
          ? `This version cannot be restored as it is: ${detail}`
          : `This version was not restored. ${describeError(failure)}`,
      );
    },
  });
  const current = profile.sections[section];
  const unchanged =
    version.data !== undefined && sameData(version.data.data, current);

  const doRestore = () => {
    if (version.data) restore.mutate(version.data.data);
  };

  return (
    <div className="flex flex-col gap-4">
      <Button
        variant="ghost"
        size="sm"
        className="w-fit md:hidden"
        onClick={onBack}
      >
        <ArrowLeft aria-hidden="true" />
        All versions
      </Button>
      {version.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading revision {revision}
        </p>
      ) : version.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Revision {revision} could not be loaded</AlertTitle>
          <AlertDescription>{describeError(version.error)}</AlertDescription>
        </Alert>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-0.5">
              <h3 className="text-base font-semibold">
                Revision {revision}
                {isCurrent ? " (current)" : ""}
              </h3>
              <p className="text-sm text-muted-foreground">
                Saved {relativeTime(version.data.created_at)},{" "}
                {absoluteTime(version.data.created_at)}
              </p>
            </div>
            <Button
              disabled={isCurrent || unchanged || restore.isPending}
              aria-busy={restore.isPending}
              onClick={() => {
                if (dirty) setConfirming(true);
                else doRestore();
              }}
            >
              {restore.isPending ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : (
                <RotateCcw aria-hidden="true" />
              )}
              {restore.isPending ? "Restoring" : "Restore this version"}
            </Button>
          </div>
          {isCurrent || unchanged ? (
            <p className="text-sm text-muted-foreground">
              {isCurrent
                ? "This is the current version."
                : "This version holds the same data as the current one."}
            </p>
          ) : null}
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Differences
            section={section}
            revision={revision}
            before={version.data.data}
            current={current}
          />
        </>
      )}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              Your changes to {label} are not saved. Restoring revision{" "}
              {revision} replaces them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setConfirming(false);
              }}
            >
              Keep editing
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirming(false);
                doRestore();
              }}
            >
              Discard and restore
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={stale} onOpenChange={setStale}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Your profile changed elsewhere</AlertDialogTitle>
            <AlertDialogDescription>
              It was saved from another tab or device after you opened this
              page. The comparison now shows the latest {label.toLowerCase()} as
              current. Restore revision {revision} over it anyway, or cancel.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setStale(false);
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                setStale(false);
                doRestore();
              }}
            >
              Restore anyway
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function Differences({
  section,
  revision,
  before,
  current,
}: {
  section: SectionName;
  revision: number;
  before: unknown;
  current: unknown;
}) {
  if (section === "facts")
    return (
      <TextDifferences revision={revision} before={before} current={current} />
    );
  return (
    <FieldDifferences
      section={section}
      revision={revision}
      before={before}
      current={current}
    />
  );
}

function TextDifferences({
  revision,
  before,
  current,
}: {
  revision: number;
  before: unknown;
  current: unknown;
}) {
  const ops = diffLines(factsText(current), factsText(before));
  const added = ops.filter((op) => op.kind === "added").length;
  const removed = ops.filter((op) => op.kind === "removed").length;
  return (
    <section aria-label="Differences" className="flex flex-col gap-2">
      <p className="text-sm text-muted-foreground">
        {added === 0 && removed === 0
          ? "No difference with the current version."
          : `${String(added)} ${added === 1 ? "line" : "lines"} only in revision ${String(revision)}, ${String(removed)} only in the current version.`}
      </p>
      <ol
        aria-label="Lines of the fact base"
        className="overflow-x-auto rounded-md border font-mono text-xs leading-relaxed"
      >
        {ops.map((op, index) => (
          <li
            key={index}
            data-kind={op.kind}
            className={cn(
              "flex gap-2 px-2 whitespace-pre-wrap [overflow-wrap:anywhere]",
              op.kind === "added" &&
                "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
              op.kind === "removed" &&
                "bg-red-50 text-red-900 dark:bg-red-950/50 dark:text-red-200",
            )}
          >
            <span aria-hidden="true" className="w-3 shrink-0 select-none">
              {op.kind === "added" ? (
                <Plus className="mt-1 size-3" />
              ) : op.kind === "removed" ? (
                <Minus className="mt-1 size-3" />
              ) : null}
            </span>
            {op.kind === "added" ? (
              <span className="sr-only">Only in revision {revision}: </span>
            ) : op.kind === "removed" ? (
              <span className="sr-only">Only in the current version: </span>
            ) : null}
            <span>{op.text === "" ? " " : op.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

const STATUS_TEXT: Record<Exclude<FieldDiff["status"], "same">, string> = {
  changed: "Changed",
  added: "Only in this version",
  removed: "Only in the current version",
};

function FieldDifferences({
  section,
  revision,
  before,
  current,
}: {
  section: SectionName;
  revision: number;
  before: unknown;
  current: unknown;
}) {
  const id = useId();
  const [onlyChanges, setOnlyChanges] = useState(false);
  const rows = diffFields(
    flattenSection(section, before),
    flattenSection(section, current),
  );
  const changed = rows.filter((row) => row.status !== "same").length;
  const shown = onlyChanges
    ? rows.filter((row) => row.status !== "same")
    : rows;
  return (
    <section aria-label="Differences" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {changed === 0
            ? "No difference with the current version."
            : `${String(changed)} ${changed === 1 ? "field differs" : "fields differ"} from the current version.`}
        </p>
        <label htmlFor={id} className="flex items-center gap-2 text-sm">
          <input
            id={id}
            type="checkbox"
            className="size-4 accent-primary"
            checked={onlyChanges}
            onChange={(event) => {
              setOnlyChanges(event.target.checked);
            }}
          />
          Only differences
        </label>
      </div>
      <table
        aria-label={`Revision ${String(revision)} and the current version`}
        className="w-full table-fixed border-collapse text-xs sm:text-sm"
      >
        <thead>
          <tr className="border-b text-left">
            <th scope="col" className="w-[34%] py-2 pr-2 font-medium">
              Field
            </th>
            <th scope="col" className="py-2 pr-2 font-medium">
              Revision {revision}
            </th>
            <th scope="col" className="py-2 font-medium">
              Current
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr
              key={row.path}
              data-status={row.status}
              className={cn(
                "border-b align-top",
                row.status !== "same" && "bg-amber-50 dark:bg-amber-950/40",
              )}
            >
              <th
                scope="row"
                className="py-2 pr-2 text-left font-normal [overflow-wrap:anywhere]"
              >
                <span className="font-medium">{row.label}</span>
                {row.status !== "same" ? (
                  <span className="mt-1 block text-xs font-medium text-amber-800 dark:text-amber-300">
                    {STATUS_TEXT[row.status]}
                  </span>
                ) : null}
              </th>
              <td className="py-2 pr-2 whitespace-pre-wrap [overflow-wrap:anywhere]">
                <Value value={row.revision} />
              </td>
              <td className="py-2 whitespace-pre-wrap [overflow-wrap:anywhere]">
                <Value value={row.current} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Value({ value }: { value: string | undefined }) {
  if (value === undefined || value === "")
    return <span className="text-muted-foreground italic">Empty</span>;
  return <>{value}</>;
}
