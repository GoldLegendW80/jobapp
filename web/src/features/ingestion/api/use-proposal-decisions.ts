import { useMutation } from "@tanstack/react-query";
import { useState, type RefObject } from "react";

import { errorDetail, isApiError } from "@/lib/api";

import {
  acceptProposal,
  rejectProposal,
  undoProposal,
  type ProposalItem,
} from "./proposals-api";
import { refusal } from "../helpers/proposal-outcomes";
import { useProposalRefresh } from "./use-proposal-refresh";

/**
 * The decisions on one proposal: accept (as proposed, as picked or as edited), reject and take
 * back, with the edit form's state and what the server refused. After each kept decision the
 * focus stays on the proposal's `heading`, since the buttons go with the decision.
 */
export function useProposalDecisions({
  item,
  title,
  runId,
  revision,
  onSaid,
  heading,
}: {
  item: ProposalItem;
  title: string;
  runId: string;
  /** The revision of the profile the list was read against. */
  revision: number;
  /** Said to a screen reader once a decision is kept. */
  onSaid: (sentence: string) => void;
  heading: RefObject<HTMLHeadingElement>;
}) {
  const [editing, setEditing] = useState(false);
  const [problem, setProblem] = useState<string>();
  /** The server's sentence for an edited value it refused (a 422 while editing). */
  const [refused, setRefused] = useState<string>();
  const refresh = useProposalRefresh(runId);

  const kept = (sentence: string) => {
    setEditing(false);
    onSaid(sentence);
    heading.current?.focus();
    return refresh();
  };
  const failed = (error: unknown) => {
    const detail = errorDetail(error);
    if (editing && detail && isApiError(error) && error.status === 422)
      setRefused(detail);
    else setProblem(refusal(error));
    // Decided elsewhere, or the profile moved: show what is true now.
    if (isApiError(error) && (error.status === 409 || error.status === 404))
      return refresh();
    return undefined;
  };

  const accept = useMutation({
    mutationFn: (edited?: { value: unknown }) =>
      acceptProposal(item.id, {
        profile_revision: revision,
        ...(edited ? { edited_value: edited.value } : {}),
      }),
    onMutate: () => {
      setProblem(undefined);
      setRefused(undefined);
    },
    onSuccess: () => kept(`Accepted: ${title}.`),
    onError: failed,
  });
  const reject = useMutation({
    mutationFn: () => rejectProposal(item.id),
    onMutate: () => {
      setProblem(undefined);
    },
    onSuccess: () => kept(`Rejected: ${title}.`),
    onError: failed,
  });
  const takeBack = useMutation({
    mutationFn: () => undoProposal(item.id),
    onMutate: () => {
      setProblem(undefined);
    },
    onSuccess: () => kept(`Taken back: ${title}.`),
    onError: failed,
  });

  return {
    editing,
    problem,
    refused,
    accept,
    reject,
    takeBack,
    pending: accept.isPending || reject.isPending || takeBack.isPending,
    startEditing: () => {
      setProblem(undefined);
      setEditing(true);
    },
    stopEditing: () => {
      setEditing(false);
      setRefused(undefined);
      heading.current?.focus();
    },
  };
}
