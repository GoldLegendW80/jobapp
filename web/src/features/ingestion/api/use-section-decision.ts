import { useMutation } from "@tanstack/react-query";
import { useState, type RefObject } from "react";

import { decideSection, type ProposalSection } from "./proposals-api";
import {
  outcomeOf,
  refusal,
  stoppedBy,
  type SectionShown,
} from "../helpers/proposal-outcomes";
import { useProposalRefresh } from "./use-proposal-refresh";

/**
 * "Accept all in this section" and "Reject the rest of this section", on the proposals shown
 * only. A refused bulk accept that names a proposal of the section marks it as `stopped`.
 */
export function useSectionDecision({
  runId,
  section,
  shown,
  heading,
}: {
  runId: string;
  section: ProposalSection;
  shown: SectionShown;
  heading: RefObject<HTMLHeadingElement>;
}) {
  const [outcome, setOutcome] = useState<string>();
  const [problem, setProblem] = useState<string>();
  /** The proposal that stopped the last bulk accept of this section, with the server's words. */
  const [stopped, setStopped] = useState<{ id: string; message: string }>();
  const refresh = useProposalRefresh(runId);
  const { asked, pending, behind } = shown;

  const decide = useMutation({
    mutationFn: (what: "accept" | "reject") =>
      decideSection(
        runId,
        what,
        section.section,
        (what === "accept" ? asked : pending).map((item) => item.id),
      ),
    onMutate: () => {
      setOutcome(undefined);
      setProblem(undefined);
      setStopped(undefined);
    },
    onSuccess: async (done, what) => {
      setOutcome(
        `${outcomeOf(done, what)}${
          behind > 0
            ? ` ${String(behind)} more in this section are not shown yet and still undecided. Show more brings them.`
            : ""
        }`,
      );
      await refresh();
      // The buttons may be gone with what they decided: the focus goes to the section.
      heading.current?.focus();
    },
    onError: async (error) => {
      const id = stoppedBy(error);
      // The proposal that stopped it carries the alert, and takes the focus.
      if (id && pending.some((item) => item.id === id))
        setStopped({ id, message: refusal(error) });
      else setProblem(refusal(error));
      await refresh();
    },
  });

  return { decide, outcome, problem, stopped };
}
