import { Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { SavedSearch, SearchRun } from "@/features/jobs/api/searches-api";
import {
  useDeleteSearch,
  useDuplicateSearch,
  useRenameSearch,
  useRunAll,
  useRunSearch,
  useSavedSearches,
  useTakeBack,
} from "@/features/jobs/api/use-saved-searches";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { runWords, searchOf } from "@/features/jobs/helpers/saved-searches";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ScheduleDialog } from "@/components/shared/jobs/schedule-dialog";
import { COLUMN } from "@/components/shared/jobs/setup-grid";
import { SearchRow } from "./search-row";
import { BODY } from "./setup-surface";

/** At most this many saved searches beside the main one (api searches.SAVED_MAX). */
const SAVED_MAX = 20;

function failed(action: string, error: unknown) {
  toast.error(`${action}. ${errorDetail(error) ?? describeError(error)}`);
}

/** What Run all did, in one sentence: the searches started and those waiting in the line. */
function runAllWords(runs: readonly SearchRun[], names: Map<string, string>) {
  const name = (run: SearchRun) => names.get(run.search_id) ?? "A search";
  const started = runs.filter((run) => run.outcome === "started").map(name);
  const queued = runs.filter((run) => run.outcome === "queued").map(name);
  const left = runs.filter(
    (run) => run.outcome !== "started" && run.outcome !== "queued",
  ).length;
  return [
    started.length ? `${started.join(", ")} started.` : "",
    queued.length ? `${queued.join(", ")} wait in line.` : "",
    left ? `${String(left)} had nothing to search.` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

/**
 * Your searches, the first screen of Find jobs (jobs J33): the main search, then the saved ones,
 * each with Run now and Edit, with New search and Run all above them. What applies to every
 * search is named once, with a link to Settings, Search.
 */
export function SearchesList({
  runner,
  onEdit,
  onNew,
  onStarted,
}: {
  runner: SweepRunner;
  onEdit: (id: string) => void;
  onNew: () => void;
  /** A search started, or its new postings open in the list: Find jobs closes, so they show on
   * the page. */
  onStarted: () => void;
}) {
  const searches = useSavedSearches();
  const items = searches.data?.items ?? [];
  const names = items.filter((item) => !item.main).map((item) => item.name);
  const running = runner.running?.search ? searchOf(runner.running) : null;

  const started = (run: SearchRun) => {
    if (!run.sweep) return false;
    runner.follow(run.sweep);
    onStarted();
    return true;
  };
  const run = useRunSearch({
    onSuccess: (result) => {
      if (started(result)) return;
      const words = runWords(
        items.find((item) => item.id === result.search_id)?.name ??
          "The search",
        result,
      );
      if (result.outcome === "queued" || result.outcome === "started")
        toast.info(words);
      else toast.error(words);
    },
    onError: (error) => {
      failed("The search did not start", error);
    },
  });
  const runAll = useRunAll({
    onSuccess: (runs) => {
      const first = runs.find((one) => one.sweep);
      toast.info(
        runAllWords(runs, new Map(items.map((item) => [item.id, item.name]))) ||
          "Nothing to search.",
      );
      if (first) started(first);
    },
    onError: (error) => {
      failed("The searches did not start", error);
    },
  });
  const takeBack = useTakeBack({
    onSuccess: () => toast.success("Taken back."),
    onError: (error) => {
      failed("Not taken back", error);
    },
  });
  const remove = useDeleteSearch({
    onSuccess: () => toast.success("Search deleted."),
    onError: (error) => {
      failed("Not deleted", error);
    },
  });
  const duplicate = useDuplicateSearch({
    onSuccess: (copy) => toast.success(`${copy.name} added.`),
    onError: (error) => {
      failed("Not duplicated", error);
    },
  });
  const rename = useRenameSearch({});
  // The search whose schedule is open: the list's newest copy of it, the last one seen once gone.
  const [scheduling, setScheduling] = useState<SavedSearch | null>(null);
  const scheduled = scheduling
    ? (items.find((item) => item.id === scheduling.id) ?? scheduling)
    : null;
  const busy =
    run.isPending ||
    runAll.isPending ||
    takeBack.isPending ||
    remove.isPending ||
    duplicate.isPending;

  const renamed = (search: SavedSearch) => async (name: string) => {
    try {
      await rename.mutateAsync({ search, name });
      return null;
    } catch (error) {
      if (
        isApiError(error) &&
        error.status === 409 &&
        (error.body as { revision?: unknown } | null)?.revision !== undefined
      )
        return "This search changed in another tab. Try again.";
      return `Not renamed. ${errorDetail(error) ?? describeError(error)}`;
    }
  };

  return (
    <div className={BODY}>
      <div className={cn(COLUMN, "flex flex-col gap-4")}>
        <p className="text-sm text-muted-foreground">
          Every search uses your never apply rules, the minimum fit, what you
          aim for, the pay and the permissions of the job websites, set once in{" "}
          <Link to="/settings/search">Settings, Search</Link>.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={onNew}
            disabled={busy || names.length >= SAVED_MAX}
          >
            <Plus aria-hidden="true" />
            New search
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              runAll.mutate();
            }}
            disabled={busy || !items.length}
          >
            Run all
          </Button>
        </div>
        {searches.isPending ? (
          <p role="status" className="flex items-center gap-2 text-sm">
            <Spinner aria-hidden="true" role={undefined} />
            Loading your searches…
          </p>
        ) : searches.isError ? (
          <div role="alert" className="flex flex-col items-start gap-2 text-sm">
            <p>
              Your searches could not be loaded. {describeError(searches.error)}
            </p>
            <Button variant="outline" onClick={() => void searches.refetch()}>
              Try again
            </Button>
          </div>
        ) : (
          <ul aria-label="Your searches" className="flex flex-col divide-y">
            {items.map((search) => (
              <SearchRow
                key={search.id}
                search={search}
                running={running === search.id}
                names={names.filter((one) => one !== search.name)}
                disabled={busy}
                onRun={() => {
                  run.mutate(search.id);
                }}
                onEdit={() => {
                  onEdit(search.id);
                }}
                onSchedule={() => {
                  setScheduling(search);
                }}
                onDuplicate={() => {
                  duplicate.mutate({ search, names });
                }}
                onRename={renamed(search)}
                onDelete={() => {
                  remove.mutate(search.id);
                }}
                onTakeBack={() => {
                  takeBack.mutate(search.id);
                }}
                onOpenPostings={onStarted}
              />
            ))}
          </ul>
        )}
      </div>
      {scheduled ? (
        <ScheduleDialog
          state={scheduled.schedule}
          search={scheduled.main ? undefined : scheduled}
          onClose={() => {
            setScheduling(null);
          }}
        />
      ) : null}
    </div>
  );
}
