/**
 * A status set on many postings at once from the bulk bar: shown in every cached list page at
 * once, rolled back when the api refuses, and undone from the toast for a while after.
 */
import {
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { describeError, errorDetail, isApiError } from "@/lib/api";

import { bulkUpdate, jobsKey, undoBulk, type JobListPage } from "./jobs-api";
import { statusLabel } from "../helpers/labels";
import { plural } from "../helpers/selection";

const LIST_KEY: QueryKey = [...jobsKey, "list"];

type ListData = InfiniteData<JobListPage, string | null>;

/** Set `status` on the given rows in every cached list page; returns the snapshot to restore. */
function setCachedStatus(
  client: QueryClient,
  ids: ReadonlySet<string>,
  status: string,
) {
  const snapshot = client.getQueriesData<ListData>({ queryKey: LIST_KEY });
  client.setQueriesData<ListData>({ queryKey: LIST_KEY }, (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            items: page.items.map((item) =>
              ids.has(item.id) ? { ...item, status } : item,
            ),
          })),
        }
      : data,
  );
  return snapshot;
}

export function useBulkStatus() {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    void client.invalidateQueries({ queryKey: jobsKey });
  };

  const undo = (token: string) => {
    undoBulk(token).then(
      (result) => {
        const skipped = result.skipped.length
          ? ` ${plural(result.skipped.length, "posting")} changed since and kept ${result.skipped.length === 1 ? "its" : "their"} new status.`
          : "";
        toast.success(
          `Undone: ${plural(result.restored.length, "posting")} back to ${result.restored.length === 1 ? "its" : "their"} previous status.${skipped}`,
        );
        refresh();
      },
      (error: unknown) => {
        toast.error(
          isApiError(error) && (error.status === 410 || error.status === 409)
            ? (errorDetail(error) ?? "Nothing to undo.")
            : `The undo failed. ${describeError(error)}`,
        );
        refresh();
      },
    );
  };

  /** Sets `status` on the postings `ids`; `onDone` once the api took it. */
  const setStatus = (ids: string[], status: string, onDone: () => void) => {
    const snapshot = setCachedStatus(client, new Set(ids), status);
    setBusy(true);
    bulkUpdate(ids, { status }).then(
      (result) => {
        setBusy(false);
        onDone();
        refresh();
        const message = `${plural(result.changed, "posting")} set to ${statusLabel(status)}${result.unchanged ? `, ${result.unchanged} already were` : ""}.`;
        const token = result.undo_token;
        if (token)
          toast.success(message, {
            duration: 15_000,
            action: {
              label: "Undo",
              onClick: () => {
                undo(token);
              },
            },
          });
        else toast.success(message);
      },
      (error: unknown) => {
        setBusy(false);
        for (const [key, data] of snapshot) client.setQueryData(key, data);
        toast.error(`Nothing was changed. ${describeError(error)}`);
        refresh();
      },
    );
  };

  return { busy, setStatus, refresh };
}
