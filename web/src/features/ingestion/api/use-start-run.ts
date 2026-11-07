import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  acceptConsent,
  actionFailure,
  consentKey,
  getConsent,
  getEstimate,
  isActive,
  refreshRuns,
  runKey,
  runsKey,
  startRefusal,
  startRun,
  type Consent,
  type Estimate,
  type Run,
  type RunList,
  type Selection,
} from "./runs-api";
import { selectionRefusal } from "../helpers/run-selection";

/**
 * The start of a run: the estimate of the selection (asked again when the documents or the
 * "Read again" choices change), the consent box ticked for one version of its text, and the start
 * with the `quote_id` of the estimate on screen. A start the server refuses says why in `notice`,
 * which takes the focus, and waits for another click.
 */
export function useStartRun({
  documentIds,
  onStarted,
}: {
  documentIds: string[];
  onStarted?: (run: Run) => void;
}) {
  const client = useQueryClient();
  const [reread, setReread] = useState<readonly string[]>([]);
  /** The estimate on screen, with the selection it was asked for. */
  const [shown, setShown] = useState<{
    estimate: Estimate;
    selection: Selection;
  }>();
  const [notice, setNotice] = useState<string>();
  const [failure, setFailure] = useState<string>();
  /** Reasons the start itself gave for documents, by id. */
  const [refused, setRefused] = useState<Record<string, string>>({});
  /** The version of the consent text the box was ticked for, never a bare yes. */
  const [tickedVersion, setTickedVersion] = useState<number | null>(null);
  const noticeRef = useRef<HTMLDivElement>(null);

  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
  });

  const ask = useMutation({
    mutationFn: (selection: Selection) =>
      getEstimate(selection).then((estimate) => ({ estimate, selection })),
    onSuccess: (answer) => {
      setShown(answer);
      setFailure(undefined);
    },
    onError: (error) => {
      setFailure(
        selectionRefusal(error) ??
          actionFailure(
            error,
            "The estimate could not be computed. Try again in a moment.",
          ),
      );
    },
  });
  const askEstimate = ask.mutate;
  const key = documentIds.join(",");
  useEffect(() => {
    if (key === "") return;
    askEstimate({
      document_ids: key.split(","),
      reread_document_ids: [...reread],
    });
  }, [askEstimate, key, reread]);

  const say = useCallback((sentence: string) => {
    setNotice(sentence);
    // The notice takes the focus so a screen reader hears it and the next Tab is next to it.
    window.requestAnimationFrame(() => noticeRef.current?.focus());
  }, []);

  const start = useMutation({
    mutationFn: async (current: {
      estimate: Estimate;
      selection: Selection;
      consent: Consent;
    }): Promise<Run> => {
      if (!current.consent.accepted)
        client.setQueryData(
          consentKey,
          await acceptConsent(current.consent.version),
        );
      return startRun(current.selection, current.estimate.quote_id);
    },
    onSuccess: (run) => {
      client.setQueryData(runKey(run.id), run);
      client.setQueryData<RunList>(runsKey, (old) => ({
        runs: [
          {
            id: run.id,
            status: run.status,
            partial: run.partial,
            created_at: run.created_at,
            finished_at: run.finished_at,
          },
          ...(old?.runs ?? []).filter((one) => one.id !== run.id),
        ],
        active_run_id: isActive(run.status) ? run.id : null,
      }));
      refreshRuns(client);
      onStarted?.(run);
    },
    onError: (error, current) => {
      const refusal = startRefusal(error);
      setFailure(undefined);
      setRefused({});
      switch (refusal.kind) {
        case "estimate_changed":
          // The new figures are on screen; nothing starts until the next click.
          setShown({
            estimate: refusal.estimate,
            selection: current.selection,
          });
          say(
            "The estimate has changed since you read it. Nothing was started. Read the new figures, then start again.",
          );
          void consent.refetch();
          break;
        case "consent_required":
          setTickedVersion(null);
          say(refusal.sentence);
          void consent.refetch();
          break;
        case "active_run":
          toast.info("A run is already in progress. This page shows it.");
          void client.invalidateQueries({ queryKey: runsKey });
          break;
        case "unreadable_documents":
          setRefused(
            Object.fromEntries(
              refusal.documents.map((one) => [one.id, one.reason]),
            ),
          );
          say(refusal.sentence);
          askEstimate(current.selection);
          break;
        case "other":
          if (!current.consent.accepted) void consent.refetch();
          say(refusal.sentence);
          break;
      }
    },
  });

  const estimate = shown?.estimate;
  const text = consent.data;
  const ticked = text !== undefined && tickedVersion === text.version;
  const ready =
    estimate !== undefined &&
    shown !== undefined &&
    text !== undefined &&
    estimate.can_start &&
    !ask.isPending;

  return {
    consent,
    estimate,
    notice,
    noticeRef,
    failure,
    refused,
    reread,
    ticked,
    /** A text other than the one the box was ticked for: the tick no longer counts. */
    changed:
      text !== undefined &&
      tickedVersion !== null &&
      tickedVersion !== text.version,
    agreed: text !== undefined && (text.accepted || ticked),
    ready,
    estimating: ask.isPending,
    starting: start.isPending,
    tick: (version: number | null) => {
      setTickedVersion(version);
    },
    toggleReread: (id: string, on: boolean) => {
      setReread((old) =>
        on ? [...old, id].sort() : old.filter((one) => one !== id),
      );
    },
    begin: () => {
      if (!ready) return;
      start.mutate({
        estimate,
        selection: shown.selection,
        consent: text,
      });
    },
  };
}
