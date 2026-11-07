import {
  useMutation,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import { useState } from "react";

import { describeError } from "@/lib/api";

import {
  answerQuestion,
  applicationKey,
  applicationsKey,
  approvalKey,
  approveApplication,
  cancelApplication,
  confirmApplication,
  refusalOf,
  saveAnswer,
  startApplication,
  type Answer,
  type Application,
  type Approval,
} from "./apply-api";
import { currentFormOf, emailToOf } from "../helpers/approval-form";
import { DECISIONS, type DecisionKind } from "../helpers/decisions";

const STALE_NOTICE =
  "The form or your documents changed since you looked. Check the answers shown now and approve again.";

/**
 * What the person does in the Apply section of a posting: start (Apply or Apply myself), decide
 * after a confirmation, save an answer, approve the form shown. Every outcome shows the server's
 * state; a refusal or a failure also leaves its sentence in `notice`.
 */
export function useApplicationActions(jobId: string, id: string | undefined) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [asking, setAsking] = useState<DecisionKind | null>(null);
  const clearNotice = () => {
    setNotice(null);
  };

  const settle = (changed?: Application) => {
    if (changed) {
      queryClient.setQueryData(applicationsKey(jobId), changed);
    }
    void queryClient.invalidateQueries({ queryKey: applicationsKey(jobId) });
    if (id !== undefined) {
      void queryClient.invalidateQueries({ queryKey: approvalKey(id) });
      void queryClient.invalidateQueries({ queryKey: applicationKey(id) });
    }
  };
  // Whatever the api refused or whatever timed out, the server may have moved on: show its state.
  const failed = (error: unknown) => {
    const refusal = refusalOf(error);
    setNotice(refusal?.detail ?? describeError(error));
    const state = refusal?.state;
    if (state) {
      queryClient.setQueryData<Application | null>(
        applicationsKey(jobId),
        (old) => (old && old.state !== state ? { ...old, state } : old),
      );
    }
    settle();
  };

  const start = useMutation({
    mutationFn: (mode: "apply" | "manual") => startApplication(jobId, mode),
    onMutate: clearNotice,
    onSuccess: (started) => {
      settle(started);
    },
    onError: failed,
  });
  const decision = useMutation({
    mutationFn: (kind: DecisionKind) => DECISIONS[kind].call(id as string),
    onMutate: clearNotice,
    onSuccess: (changed) => {
      setAsking(null);
      settle(changed);
    },
    onError: (error) => {
      setAsking(null);
      failed(error);
    },
  });
  const save = useMutation({
    mutationFn: ({
      answer,
      value,
    }: {
      answer: Answer;
      value: string | boolean;
    }) => saveAnswer(id as string, answer.field_key, value),
    onMutate: clearNotice,
    // The answer changes the form's hash: show the approval the api answered with, so Approve
    // never offers the old one, and read the state of the application again.
    onSuccess: (changed) => {
      queryClient.setQueryData(approvalKey(id as string), changed);
      void queryClient.invalidateQueries({ queryKey: applicationsKey(jobId) });
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
        id as string,
        current.answers_hash,
        emailToOf(current),
        acknowledged,
      ),
    onMutate: clearNotice,
    onSuccess: (changed) => {
      setConfirming(false);
      settle(changed);
    },
    onError: (error) => {
      setConfirming(false);
      const form = currentFormOf(refusalOf(error));
      if (form) {
        // Show the form as it is now; the old hash is never sent again.
        queryClient.setQueryData<Approval>(approvalKey(id as string), (old) =>
          old ? { ...old, ...form } : old,
        );
        setNotice(STALE_NOTICE);
        void queryClient.invalidateQueries({
          queryKey: applicationsKey(jobId),
        });
        return;
      }
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

  return {
    notice,
    asking,
    setAsking,
    confirming,
    setConfirming,
    settle,
    failed,
    start,
    decision,
    save,
    saveOne,
    approve,
    busy:
      start.isPending ||
      decision.isPending ||
      approve.isPending ||
      save.isPending,
  };
}

type Handlers<Variables> = Pick<
  UseMutationOptions<Application, Error, Variables>,
  "onMutate" | "onSuccess" | "onError"
>;

/** Cancel during a Full auto hold; whatever the answer, Apply is read again. */
export function useCancelHold(applicationId: string, handlers: Handlers<void>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => cancelApplication(applicationId),
    ...handlers,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["apply"] });
    },
  });
}

/** Confirm and send the fields the assistant filled, exactly as the hash covers them, or Cancel. */
export function useConfirmFilled(
  applicationId: string,
  fieldsHash: string | null,
  handlers: Handlers<"confirm" | "cancel">,
) {
  return useMutation({
    mutationFn: (kind: "confirm" | "cancel") =>
      kind === "confirm"
        ? confirmApplication(applicationId, fieldsHash ?? "")
        : cancelApplication(applicationId),
    ...handlers,
  });
}

/** The person's answer to the assistant's question (empty for an action done on the site). */
export function useAnswerQuestion(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ answer, remember }: { answer: string; remember: boolean }) =>
      answerQuestion(applicationId, answer, remember),
    onSuccess: (updated) => {
      void queryClient.invalidateQueries({
        queryKey: applicationsKey(updated.posting_id),
      });
      void queryClient.invalidateQueries({
        queryKey: applicationKey(updated.id),
      });
    },
  });
}
