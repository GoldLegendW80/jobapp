import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  agentKey,
  agentsKey,
  changesOf,
  problemsOf,
  REVERT_NOTE,
  saveVersion,
  type AgentPage,
  type Editable,
  type NewVersion,
} from "./agents-api";
import { message } from "../helpers/errors";

export type DraftProblems = ReturnType<typeof problemsOf>;
export type DraftEdit =
  Partial<Editable> | ((current: Editable) => Partial<Editable>);

/** What an editable card of the agent page reads and changes. */
export interface DraftFieldProps {
  page: AgentPage;
  draft: Editable;
  problems: DraftProblems;
  onEdit: (edit: DraftEdit) => void;
}

/**
 * The agent page's draft and its save. The draft starts from what runs use now, and stays as
 * typed across refetches; only a save or revert made here, or "Start again", replaces it.
 */
export function useAgentDraft(page: AgentPage) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Editable>(page.effective);
  const [base, setBase] = useState<number | null>(page.version);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string>();
  const [reverting, setReverting] = useState(false);
  const problems = problemsOf(draft, page.choices);
  const changes = changesOf(page.code, draft);
  const unsaved =
    JSON.stringify(changes) !==
    JSON.stringify(changesOf(page.code, page.effective));

  const mutation = useMutation({
    mutationFn: (body: NewVersion) => saveVersion(page.name, body),
    onSuccess: (next) => {
      queryClient.setQueryData(agentKey(page.name), next);
      void queryClient.invalidateQueries({ queryKey: agentsKey, exact: true });
      setDraft(next.effective);
      setBase(next.version);
      setNote("");
      setError(undefined);
      setReverting(false);
      toast.success(
        `Version ${String(next.version)} saved. The next run of ${page.name} uses it.`,
      );
    },
    onError: (failure) => {
      setError(message(failure));
    },
  });

  return {
    draft,
    note,
    error,
    reverting,
    problems,
    unsaved,
    saving: mutation.isPending,
    /** A version was saved elsewhere since this page started its draft. */
    newer: page.version !== base,
    edit: (edit: DraftEdit) => {
      setDraft((current) => ({
        ...current,
        ...(typeof edit === "function" ? edit(current) : edit),
      }));
    },
    setNote,
    save: () => {
      setError(undefined);
      mutation.mutate({
        changes,
        note: note.trim() || null,
        base_version: base,
      });
    },
    startAgain: () => {
      setDraft(page.effective);
      setBase(page.version);
      setError(undefined);
    },
    askRevert: () => {
      setError(undefined);
      setReverting(true);
    },
    cancelRevert: () => {
      if (!mutation.isPending) setReverting(false);
    },
    revert: () => {
      mutation.mutate({ changes: {}, note: REVERT_NOTE, base_version: base });
    },
  };
}

export type AgentDraft = ReturnType<typeof useAgentDraft>;
