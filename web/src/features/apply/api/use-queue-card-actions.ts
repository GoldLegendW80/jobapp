import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type RefObject, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { jobsKey } from "@/features/jobs/api/jobs-api";
import { describeError, errorDetail } from "@/lib/api";

import {
  applicationsKey,
  approvalKey,
  approveApplication,
  cancelApplication,
  refusalOf,
  saveAnswer,
  type Answer,
  type Approval,
} from "./apply-api";
import { queueKey, rejectQueued, type QueueItem } from "./queue-api";
import { currentFormOf, emailToOf } from "../helpers/approval-form";
import { DECISIONS } from "../helpers/decisions";

/** The decisions of an item after the click, as the posting's Apply section offers them. */
export type AfterClick = "submitted" | "retry";

/**
 * What the person does on one card of the review queue: save an answer, approve the form shown,
 * reject (and maybe skip the posting), cancel, and after the click "It was submitted" or Try
 * again, each behind its dialog. A card that leaves (Reject, Cancel, "It was submitted") calls
 * `onLeave` once it is gone; after an approval the focus moves to the next card's title.
 * `onStaleForm` runs when Approve was refused because the form moved, to show the answers again.
 */
export function useQueueCardActions(
  item: QueueItem,
  {
    article,
    onLeave,
    onStaleForm,
  }: {
    /** The card, to find the title of the next one after an approval. */
    article: RefObject<HTMLElement | null>;
    onLeave: () => void;
    onStaleForm: () => void;
  },
) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [asking, setAsking] = useState<AfterClick | null>(null);
  const clearNotice = () => {
    setNotice(null);
  };

  // Set by a Reject or a Cancel that succeeded: the page takes the focus once the card is gone.
  const leaving = useRef(false);
  const focusAfterApproval = useRef(false);
  const approvalNextFocus = useRef<HTMLElement | null>(null);
  useEffect(
    () => () => {
      if (leaving.current) onLeave();
    },
    [onLeave],
  );

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queueKey });
    void queryClient.invalidateQueries({
      queryKey: applicationsKey(item.posting_id),
    });
    void queryClient.invalidateQueries({ queryKey: approvalKey(item.id) });
  };
  const failed = (error: unknown) => {
    setNotice(
      refusalOf(error)?.detail ?? errorDetail(error) ?? describeError(error),
    );
    refresh();
  };

  const save = useMutation({
    mutationFn: ({
      answer,
      value,
    }: {
      answer: Answer;
      value: string | boolean;
    }) => saveAnswer(item.id, answer.field_key, value),
    onMutate: clearNotice,
    // The answer changes the form's hash: Approve only ever offers the form the api answered.
    onSuccess: (changed) => {
      queryClient.setQueryData(approvalKey(item.id), changed);
      void queryClient.invalidateQueries({ queryKey: queueKey });
    },
    onError: failed,
  });
  const approve = useMutation({
    mutationFn: ({
      current,
      acknowledged,
    }: {
      current: Approval;
      acknowledged: string[];
    }) =>
      approveApplication(
        item.id,
        current.answers_hash,
        emailToOf(current),
        acknowledged,
      ),
    onMutate: clearNotice,
    onSuccess: () => {
      const cards = Array.from(
        document.querySelectorAll<HTMLElement>("[data-queue-card-title]"),
      );
      const currentTitle = article.current?.querySelector<HTMLElement>(
        "[data-queue-card-title]",
      );
      approvalNextFocus.current = currentTitle
        ? (cards[cards.indexOf(currentTitle) + 1] ?? null)
        : null;
      setConfirming(false);
      refresh();
      focusAfterApproval.current = true;
    },
    onError: (error) => {
      setConfirming(false);
      const refusal = refusalOf(error);
      const form = currentFormOf(refusal);
      if (refusal && form) {
        // Show the form as it is now; the old hash is never sent again.
        queryClient.setQueryData<Approval>(approvalKey(item.id), (old) =>
          old ? { ...old, ...form } : old,
        );
        onStaleForm();
        setNotice(refusal.detail);
        void queryClient.invalidateQueries({ queryKey: queueKey });
        return;
      }
      failed(error);
    },
  });
  const reject = useMutation({
    mutationFn: (skip: boolean) => rejectQueued(item.id, skip),
    onMutate: clearNotice,
    onSuccess: (rejected) => {
      setRejecting(false);
      toast.success(
        rejected.posting_status === "skipped"
          ? `Rejected ${item.title} at ${item.company}, and the posting is skipped. Nothing was sent.`
          : `Rejected ${item.title} at ${item.company}. Nothing was sent.`,
      );
      void queryClient.invalidateQueries({ queryKey: jobsKey });
      leaving.current = true;
      refresh();
    },
    onError: (error) => {
      setRejecting(false);
      failed(error);
    },
  });
  const cancel = useMutation({
    mutationFn: () => cancelApplication(item.id),
    onMutate: clearNotice,
    onSuccess: () => {
      setCancelling(false);
      toast.success(
        `Cancelled ${item.title} at ${item.company}. Nothing was sent.`,
      );
      leaving.current = true;
      refresh();
    },
    onError: (error) => {
      setCancelling(false);
      failed(error);
    },
  });

  // After the click: "It was submitted" ends the application (the card leaves), "Try again"
  // brings it back for a new approval (the card stays, with the form to approve).
  const decision = useMutation({
    mutationFn: (kind: AfterClick) => DECISIONS[kind].call(item.id),
    onMutate: clearNotice,
    onSuccess: (_changed, kind) => {
      setAsking(null);
      if (kind === "submitted") {
        toast.success(
          `Recorded ${item.title} at ${item.company} as submitted.`,
        );
        leaving.current = true;
      }
      void queryClient.invalidateQueries({ queryKey: jobsKey });
      refresh();
    },
    onError: (error) => {
      setAsking(null);
      failed(error);
    },
  });

  /** Saves one answer for the fields list: true once the api kept it. */
  const saveOne = async (answer: Answer, value: string | boolean) => {
    try {
      await save.mutateAsync({ answer, value });
      return true;
    } catch {
      return false;
    }
  };

  /** The approve dialog closed after an approval: the next card's title, else the page. */
  const focusAfterApprove = (event: Event) => {
    if (!focusAfterApproval.current) return;
    focusAfterApproval.current = false;
    event.preventDefault();
    const next = approvalNextFocus.current;
    approvalNextFocus.current = null;
    if (next?.isConnected) next.focus();
    else onLeave();
  };

  return {
    notice,
    confirming,
    setConfirming,
    rejecting,
    setRejecting,
    cancelling,
    setCancelling,
    asking,
    setAsking,
    save,
    saveOne,
    approve,
    reject,
    cancel,
    decision,
    focusAfterApprove,
    /** A rehearsal's answer ends the application: the card leaves once it is gone. */
    markLeaving: () => {
      leaving.current = true;
    },
    busy:
      save.isPending ||
      approve.isPending ||
      reject.isPending ||
      cancel.isPending ||
      decision.isPending,
  };
}
