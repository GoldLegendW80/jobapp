import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import type { Citation, ProposalItem } from "../../api/proposals-api";
import { useProposalDecisions } from "@/features/ingestion/api/use-proposal-decisions";
import {
  flagLabel,
  FREE_TEXT_NOTE,
  itemTitle,
  REPLACE_NOTE,
  statusLabel,
  TYPED_NOTE,
} from "@/features/ingestion/helpers/proposal-words";
import { ProposalActions, TakeBackButton } from "./proposal-actions";
import { ProposalChoices, type Choice } from "./proposal-choices";
import { ProposalComparison } from "./proposal-comparison";
import { EditForm } from "./proposal-edit";

/** What every proposal of a section shares with the list around it. */
export interface ProposalContext {
  runId: string;
  /** The revision of the profile this list was read against. */
  revision: number;
  onOpen: (citation: Citation) => void;
  /** Said to a screen reader once a decision is kept. */
  onSaid: (sentence: string) => void;
  /** A bulk decision of this section is in flight: the single buttons wait for it. */
  sectionBusy: boolean;
  /** The proposal that stopped the last bulk accept of this section, with the server's words. */
  stopped?: { id: string; message: string };
}

/** One proposal: what it changes, where it comes from, and its decisions. */
export function ProposalCard({
  item,
  context,
}: {
  item: ProposalItem;
  context: ProposalContext;
}) {
  const { runId, revision, onOpen, onSaid, sectionBusy, stopped } = context;
  const title = itemTitle(item);
  const decided = statusLabel(item.status);
  const pending = item.status === "pending";
  const choices: Choice[] =
    item.conflict.length > 0
      ? [{ value: item.value, citations: item.citations }, ...item.conflict]
      : [];
  const heading = useRef<HTMLHeadingElement>(null);
  const [chosen, setChosen] = useState<number>();
  const decisions = useProposalDecisions({
    item,
    title,
    runId,
    revision,
    onSaid,
    heading,
  });
  const { editing, problem, accept } = decisions;
  const stoppedHere = stopped?.id === item.id ? stopped.message : undefined;
  // A bulk accept this proposal stopped: the focus goes to it, where the alert says why.
  useEffect(() => {
    if (stoppedHere) heading.current?.focus();
  }, [stoppedHere]);
  const busy = decisions.pending || sectionBusy;
  const invalid = item.status === "invalid" || item.bulk === "invalid";
  const picked = chosen === undefined ? undefined : choices[chosen];
  return (
    <li
      data-testid="proposal"
      data-key={item.key}
      data-status={item.status}
      className={cn(invalid && "text-muted-foreground")}
    >
      <Card className={cn("gap-2 p-4", invalid && "bg-muted/40 text-inherit")}>
        <div className="flex flex-wrap items-center gap-2">
          <h5
            ref={heading}
            tabIndex={-1}
            className="text-sm font-medium break-words outline-none focus-visible:underline"
          >
            {title}
          </h5>
          {decided ? <Badge variant="secondary">{decided}</Badge> : null}
          {item.stale ? <Badge variant="outline">Check again</Badge> : null}
          {item.locked && item.bulk === "locked" ? (
            <Badge variant="outline">Accepted one by one</Badge>
          ) : null}
          {pending && item.bulk === "typed_by_you" ? (
            <Badge variant="outline">Your own value</Badge>
          ) : null}
          {item.flags.map((flag) => (
            <Badge key={flag} variant="outline">
              {flagLabel(flag)}
            </Badge>
          ))}
        </div>
        {choices.length > 0 ? (
          <ProposalChoices
            item={item}
            title={title}
            choices={choices}
            chosen={chosen}
            disabled={!pending || busy}
            onChoose={setChosen}
            onOpen={onOpen}
          />
        ) : (
          <ProposalComparison item={item} title={title} onOpen={onOpen} />
        )}
        {item.stale ? (
          <p className="text-sm text-muted-foreground">
            {item.stale_reason === "depends_on_changed"
              ? "This was calculated from a proposal you have since rejected or changed. Check it again."
              : "Your profile changed here since this was proposed. Check it again."}
          </p>
        ) : null}
        {pending && item.bulk === "typed_by_you" ? (
          <p className="text-sm text-muted-foreground">{TYPED_NOTE}</p>
        ) : null}
        {pending && item.bulk === "replace" ? (
          <p className="text-sm text-muted-foreground">{REPLACE_NOTE}</p>
        ) : null}
        {item.free_text ? (
          <p className="text-xs text-muted-foreground">{FREE_TEXT_NOTE}</p>
        ) : null}
        {item.problem ? (
          <p className="text-sm text-destructive">{item.problem}</p>
        ) : null}
        {pending && editing ? (
          <EditForm
            // The form reads its value once, when it mounts: it restarts from the value picked.
            key={chosen ?? -1}
            title={title}
            value={picked ? picked.value : item.value}
            busy={busy}
            refused={decisions.refused}
            onSubmit={(value) => {
              accept.mutate({ value });
            }}
            onCancel={decisions.stopEditing}
          />
        ) : null}
        {pending && !editing ? (
          <ProposalActions
            item={item}
            title={title}
            blocked={invalid || item.bulk === "profile_conflict"}
            invalid={invalid}
            busy={busy}
            accepting={accept.isPending}
            canAccept={choices.length === 0 || picked !== undefined}
            onAccept={() => {
              accept.mutate(picked ? { value: picked.value } : undefined);
            }}
            onEdit={decisions.startEditing}
            onReject={() => {
              decisions.reject.mutate();
            }}
          />
        ) : null}
        {item.status === "accepted" ? (
          <TakeBackButton
            title={title}
            busy={busy}
            taking={decisions.takeBack.isPending}
            onTakeBack={() => {
              decisions.takeBack.mutate();
            }}
          />
        ) : null}
        {pending && choices.length > 0 && picked === undefined && !editing ? (
          <p className="text-xs text-muted-foreground">
            Pick a value to accept it.
          </p>
        ) : null}
        {problem ? (
          <p role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        ) : null}
        {stoppedHere && !problem ? (
          <p role="alert" className="text-sm text-destructive">
            {stoppedHere}
          </p>
        ) : null}
      </Card>
    </li>
  );
}
