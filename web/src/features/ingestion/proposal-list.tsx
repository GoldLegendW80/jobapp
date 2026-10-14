import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  FileText,
  LoaderCircle,
  Pencil,
  RotateCw,
  X,
} from "lucide-react";
import { useId, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { describeError, errorDetail, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { failureSentence, getDocumentText } from "./documents-api";
import { EditForm } from "./proposal-edit";
import { editable, isRecord, refusedField, words } from "./proposal-value";
import {
  acceptProposal,
  type Citation,
  completenessKey,
  decideSection,
  finishedRunKey,
  getCompleteness,
  getFinishedRunId,
  getProposals,
  type ProposalItem,
  type ProposalSection,
  proposalsKey,
  rejectProposal,
  type Review,
} from "./proposals-api";

/**
 * Step 3, third part: what a run proposes for each section of the profile, with the place in
 * the document each proposal comes from (task G12), and the decisions on them (task G13):
 * accept, edit then accept, reject, "Accept all in this section" and "Reject the rest of this
 * section". Nothing reaches the profile before a decision, and every decision is the server's:
 * the list is read again after each one, so what it shows is what was kept.
 *
 * What "accept all" leaves out (a locked fact, a flagged item, a removal, an item to check
 * again) keeps its badge and its own Accept button, and the section says how many were left.
 *
 * `runId` is the run to show. Without it the list finds the user's latest finished run by
 * itself, and renders nothing while no run has finished.
 *
 * Everything a document wrote (a value, an excerpt) is rendered as text, and an excerpt is cut
 * to EXCERPT_SHOWN characters.
 */
export const EXCERPT_SHOWN = 300;
/** Proposals of one section shown at first, and added by each "Show more". */
export const PAGE = 50;
/** Characters of the document shown before the quote when a citation is opened. */
const CONTEXT_BEFORE = 400;

const SECTION_NAMES: Record<string, string> = {
  identity: "Identity",
  experience: "Experience",
  facts: "Facts",
  skills: "Skills",
  search: "Search",
  answers: "Answers",
};

const FLAG_LABELS: Record<string, string> = {
  conflict: "Sources disagree",
  derived: "Calculated",
  transcribed: "Read from an image",
  looks_like_instruction: "Reads like an instruction",
};

const STATUS_LABELS: Record<string, string> = {
  accepted: "Accepted",
  rejected: "Rejected",
  undone: "Taken back",
  superseded: "Replaced by a newer run",
  invalid: "No longer fits",
};

export const FREE_TEXT_NOTE =
  "The quote shows where this comes from. The wording is the agent's own, so read it before you accept.";
export const EMPTY_NOTE =
  "Nothing new to propose. What these documents say is already in your profile.";

function plural(count: number, one: string, many: string): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}

function cut(text: string): string {
  const points = Array.from(text);
  return points.length > EXCERPT_SHOWN
    ? `${points.slice(0, EXCERPT_SHOWN).join("")}…`
    : text;
}

function scalar(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (Array.isArray(value)) return value.map(scalar).join(", ");
  if (isRecord(value))
    return Object.entries(value)
      .filter(([, held]) => held !== null && held !== "")
      .map(([name, held]) => `${words(name)}: ${scalar(held)}`)
      .join(", ");
  return "";
}

/** A value of the profile as text: one line for a single value, one line per field otherwise. */
function Value({ value, label }: { value: unknown; label: string }) {
  if (value === null || value === undefined || value === "")
    return <p className="text-sm text-muted-foreground">Nothing yet</p>;
  if (!isRecord(value))
    return <p className="text-sm break-words">{scalar(value)}</p>;
  const fields = Object.entries(value).filter(
    ([name, held]) => held !== null && held !== "" && name !== "key",
  );
  return (
    <dl
      aria-label={label}
      className="grid grid-cols-[auto_1fr] gap-x-3 text-sm"
    >
      {fields.map(([name, held]) => (
        <div key={name} className="contents">
          <dt className="text-muted-foreground">{words(name)}</dt>
          <dd className="min-w-0 break-words">{scalar(held)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Source({
  citation,
  onOpen,
}: {
  citation: Citation;
  onOpen: (citation: Citation) => void;
}) {
  const quote = <q className="break-words">{cut(citation.excerpt)}</q>;
  if (citation.document === null)
    return (
      <li className="text-sm">
        <span className="font-medium text-muted-foreground">
          Source deleted
        </span>{" "}
        {quote}
      </li>
    );
  return (
    <li className="text-sm">
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0 align-baseline"
        onClick={() => {
          onOpen(citation);
        }}
      >
        <FileText aria-hidden="true" />
        <bdi>{citation.document.name ?? "Document"}</bdi>
        <span className="sr-only">, open at the quote</span>
      </Button>{" "}
      {quote}
      {citation.transcribed ? (
        <span className="text-muted-foreground"> (read from an image)</span>
      ) : null}
    </li>
  );
}

function Sources({
  citations,
  label,
  onOpen,
}: {
  citations: Citation[];
  label: string;
  onOpen: (citation: Citation) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <ul aria-label={label} className="flex flex-col gap-1">
      {citations.map((citation) => (
        <Source
          key={`${citation.document_id}:${String(citation.start)}:${String(citation.end)}`}
          citation={citation}
          onOpen={onOpen}
        />
      ))}
    </ul>
  );
}

function itemTitle(item: ProposalItem): string {
  const value = isRecord(item.value) ? item.value : {};
  const named = [value.organisation, value.name, value.title, value.school]
    .filter((one): one is string => typeof one === "string" && one !== "")
    .at(0);
  const slot = words(item.slot);
  return named ? `${slot}: ${named}` : slot;
}

/** What the page says when the server refuses a decision: its own sentence when it gave one. */
function refusal(error: unknown): string {
  if (isApiError(error) && error.status === 404)
    return "This proposal is no longer there.";
  const detail = errorDetail(error);
  if (detail && isApiError(error) && error.status === 422)
    return refusedField(detail)?.message ?? detail;
  if (detail && isApiError(error) && error.status === 409) return detail;
  return describeError(error);
}

/** Read the list and the profile again: after a decision both may have moved. */
function useRefresh(runId: string) {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: proposalsKey(runId) });
    // The profile, its completeness and the revisions of the History drawer.
    void client.invalidateQueries({ queryKey: ["profile"] });
  };
}

interface Context {
  runId: string;
  /** The revision of the profile this list was read against. */
  revision: number;
  onOpen: (citation: Citation) => void;
  /** Said to a screen reader once a decision is kept. */
  onSaid: (sentence: string) => void;
}

function Item({ item, context }: { item: ProposalItem; context: Context }) {
  const { runId, revision, onOpen, onSaid } = context;
  const title = itemTitle(item);
  const decided = STATUS_LABELS[item.status];
  const pending = item.status === "pending";
  const choices =
    item.conflict.length > 0
      ? [{ value: item.value, citations: item.citations }, ...item.conflict]
      : [];
  const group = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [editing, setEditing] = useState(false);
  const [chosen, setChosen] = useState<number>();
  const [problem, setProblem] = useState<string>();
  const [refused, setRefused] = useState<string>();
  const refresh = useRefresh(runId);
  const kept = (sentence: string) => {
    setEditing(false);
    onSaid(sentence);
    refresh();
    // The buttons go with the decision: the focus stays on the proposal.
    heading.current?.focus();
  };
  const failed = (error: unknown) => {
    const detail = errorDetail(error);
    if (editing && detail && isApiError(error) && error.status === 422)
      setRefused(detail);
    else setProblem(refusal(error));
    // Decided elsewhere, or the profile moved: show what is true now.
    if (isApiError(error) && (error.status === 409 || error.status === 404))
      refresh();
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
    onSuccess: () => {
      kept(`Accepted: ${title}.`);
    },
    onError: failed,
  });
  const reject = useMutation({
    mutationFn: () => rejectProposal(item.id),
    onMutate: () => {
      setProblem(undefined);
    },
    onSuccess: () => {
      kept(`Rejected: ${title}.`);
    },
    onError: failed,
  });
  const busy = accept.isPending || reject.isPending;
  const invalid = item.status === "invalid" || item.bulk === "invalid";
  // The profile cannot take it as it is: an edit may fit, the value as proposed cannot.
  const blocked = invalid || item.bulk === "profile_conflict";
  const picked = chosen === undefined ? undefined : choices[chosen];
  return (
    <li
      data-testid="proposal"
      data-key={item.key}
      data-status={item.status}
      className={cn(
        "flex flex-col gap-2 rounded-md border p-3",
        invalid && "bg-muted/40 text-muted-foreground",
      )}
    >
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
        {item.locked ? (
          <Badge variant="outline">Accepted one by one</Badge>
        ) : null}
        {item.flags.map((flag) => (
          <Badge key={flag} variant="outline">
            {FLAG_LABELS[flag] ?? words(flag)}
          </Badge>
        ))}
      </div>
      {choices.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">
            {pending
              ? "Your documents give different values. Pick the one to keep."
              : "Your documents gave different values."}
            {pending && isRecord(item.value)
              ? " If these are two different entries, keep one here and add the other in your profile."
              : ""}
          </p>
          <div
            role="radiogroup"
            aria-label={`Values for ${title}`}
            className="flex flex-col gap-2"
          >
            {choices.map((choice, index) => (
              // The values of a conflict have no identity but their place in it.
              <div
                key={index}
                className="flex flex-col gap-1 rounded-sm border p-2 has-checked:border-primary"
              >
                <label className="flex items-start gap-2">
                  <input
                    type="radio"
                    name={group}
                    className="mt-1 size-4 shrink-0"
                    aria-label={`Value ${String(index + 1)}: ${scalar(choice.value)}`}
                    checked={chosen === index}
                    disabled={!pending || busy}
                    onChange={() => {
                      setChosen(index);
                    }}
                  />
                  <Value
                    value={choice.value}
                    label={`Value ${String(index + 1)}`}
                  />
                </label>
                <Sources
                  citations={choice.citations}
                  label={`Sources of value ${String(index + 1)}`}
                  onOpen={onOpen}
                />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <p className="text-xs text-muted-foreground">
                In your profile now
              </p>
              <Value value={item.before} label={`${title}, now`} />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">
                {item.action === "remove" ? "After: removed" : "Proposed"}
              </p>
              {item.action === "remove" ? null : (
                <Value
                  value={pending ? item.after : item.value}
                  label={`${title}, proposed`}
                />
              )}
            </div>
          </div>
          <Sources
            citations={item.citations}
            label={`Sources of ${title}`}
            onOpen={onOpen}
          />
        </>
      )}
      {item.stale ? (
        <p className="text-sm text-muted-foreground">
          {item.stale_reason === "depends_on_changed"
            ? "This was calculated from a proposal you have since rejected or changed. Check it again."
            : "Your profile changed here since this was proposed. Check it again."}
        </p>
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
          refused={refused}
          onSubmit={(value) => {
            accept.mutate({ value });
          }}
          onCancel={() => {
            setEditing(false);
            setRefused(undefined);
            heading.current?.focus();
          }}
        />
      ) : null}
      {pending && !editing ? (
        <div className="flex flex-wrap gap-2">
          {blocked ? null : (
            <Button
              size="sm"
              aria-label={`${item.action === "remove" ? "Accept the removal" : "Accept"}: ${title}`}
              disabled={busy || (choices.length > 0 && picked === undefined)}
              onClick={() => {
                accept.mutate(picked ? { value: picked.value } : undefined);
              }}
            >
              {accept.isPending ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : (
                <Check aria-hidden="true" />
              )}
              {item.action === "remove" ? "Accept the removal" : "Accept"}
            </Button>
          )}
          {invalid ||
          item.action === "remove" ||
          !editable(item.value) ? null : (
            <Button
              variant="outline"
              size="sm"
              aria-label={`Edit: ${title}`}
              disabled={busy}
              onClick={() => {
                setProblem(undefined);
                setEditing(true);
              }}
            >
              <Pencil aria-hidden="true" />
              Edit
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            aria-label={`Reject: ${title}`}
            disabled={busy}
            onClick={() => {
              reject.mutate();
            }}
          >
            <X aria-hidden="true" />
            Reject
          </Button>
        </div>
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
    </li>
  );
}

function Section({
  section,
  context,
}: {
  section: ProposalSection;
  context: Context;
}) {
  const name = SECTION_NAMES[section.section] ?? words(section.section);
  const id = `proposals-${section.section}`;
  const [shown, setShown] = useState(PAGE);
  const [outcome, setOutcome] = useState<string>();
  const [problem, setProblem] = useState<string>();
  const refresh = useRefresh(context.runId);
  // The two bulk decisions are about what is on the screen, never about a proposal the user
  // has not been shown yet.
  const listed = section.items.slice(0, shown);
  const pending = listed.filter((item) => item.status === "pending");
  // What a bulk accept may be asked: everything pending but what the page already shows as
  // not fitting. The server leaves out what is accepted one by one, and says how many.
  const asked = pending.filter(
    (item) => item.bulk !== "invalid" && item.bulk !== "profile_conflict",
  );
  const takes = asked.some((item) => item.bulk === null);
  // What the section holds beyond the page: the two buttons do not decide it, and say so.
  const behind = section.items
    .slice(shown)
    .filter((item) => item.status === "pending").length;
  const acceptable = asked.filter((item) => item.bulk === null).length;
  const accepting =
    behind > 0
      ? `Accept the ${String(acceptable)} shown`
      : "Accept all in this section";
  const rejecting =
    behind > 0
      ? `Reject the ${String(pending.length)} shown`
      : "Reject the rest of this section";
  const decide = useMutation({
    mutationFn: (what: "accept" | "reject") =>
      decideSection(
        context.runId,
        what,
        section.section,
        (what === "accept" ? asked : pending).map((item) => item.id),
      ),
    onMutate: () => {
      setOutcome(undefined);
      setProblem(undefined);
    },
    onSuccess: (done, what) => {
      const count = done.decided.length;
      const left = pending.length - count;
      setOutcome(
        `${
          what === "reject"
            ? `Rejected ${String(count)}.`
            : `Accepted ${String(count)}.${
                left > 0
                  ? ` ${String(left)} left for you to accept one by one.`
                  : ""
              }`
        }${
          behind > 0
            ? ` ${String(behind)} more in this section are not shown yet and still undecided. Show more brings them.`
            : ""
        }`,
      );
      refresh();
    },
    onError: (error) => {
      setProblem(refusal(error));
      refresh();
    },
  });
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 id={id} className="font-medium">
          {name}
        </h4>
        <p className="text-sm text-muted-foreground tabular-nums">
          {plural(section.counts.total, "proposal", "proposals")}
          {section.counts.pending < section.counts.total
            ? `, ${String(section.counts.pending)} left to review`
            : ""}
        </p>
      </div>
      {pending.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {takes ? (
            <Button
              size="sm"
              aria-label={`${accepting}: ${name}`}
              disabled={decide.isPending}
              onClick={() => {
                decide.mutate("accept");
              }}
            >
              <Check aria-hidden="true" />
              {accepting}
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            aria-label={`${rejecting}: ${name}`}
            disabled={decide.isPending}
            onClick={() => {
              decide.mutate("reject");
            }}
          >
            <X aria-hidden="true" />
            {rejecting}
          </Button>
          {takes ? null : (
            <p className="text-sm text-muted-foreground">
              What is left here is accepted one by one.
            </p>
          )}
        </div>
      ) : null}
      {/* Always in the page, so a screen reader hears the outcome when it arrives. */}
      <p role="status" className="text-sm empty:hidden">
        {outcome}
      </p>
      {problem ? (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {listed.map((item) => (
          <Item key={item.id} item={item} context={context} />
        ))}
      </ul>
      {section.items.length > shown ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm text-muted-foreground tabular-nums">
            Showing {shown} of {section.items.length}.
          </p>
          <Button
            variant="outline"
            size="sm"
            aria-label={`Show ${String(Math.min(PAGE, section.items.length - shown))} more: ${name}`}
            onClick={() => {
              setShown(shown + PAGE);
            }}
          >
            Show {Math.min(PAGE, section.items.length - shown)} more
          </Button>
        </div>
      ) : null}
    </section>
  );
}

/** The text of a document around one quote, the quote marked and scrolled into view. */
function QuotePreview({ citation }: { citation: Citation }) {
  const from = Math.max(0, citation.start - CONTEXT_BEFORE);
  const query = useQuery({
    queryKey: ["ingestion", "documents", citation.document_id, "quote", from],
    queryFn: ({ signal }) =>
      getDocumentText(citation.document_id, from, signal),
    // Dropped as soon as the dialog closes: a document's text is not kept around.
    gcTime: 0,
  });
  // Offsets count characters as the api does, not UTF-16 units.
  const points = Array.from(query.data?.text ?? "");
  const offset = query.data?.offset ?? from;
  const start = citation.start - offset;
  const end = citation.end - offset;
  const quoted = points.slice(Math.max(0, start), Math.max(0, end)).join("");
  const found = start >= 0 && quoted !== "" && end <= points.length;
  return (
    <>
      <DialogHeader>
        <DialogTitle className="pr-6 break-words">
          <bdi>{citation.document?.name ?? "Document"}</bdi>
        </DialogTitle>
        <DialogDescription>
          The text kept from this document, at the place of the quote.
        </DialogDescription>
      </DialogHeader>
      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the text
        </p>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          The text could not be loaded. {failureSentence(query.error)}
        </p>
      ) : (
        <div
          role="region"
          aria-label="Text of the document"
          tabIndex={0}
          className="max-h-[55vh] overflow-y-auto rounded-md border bg-muted/40 p-3"
        >
          {found ? (
            <pre className="font-sans text-sm leading-relaxed break-words whitespace-pre-wrap">
              {points.slice(0, start).join("")}
              <mark
                data-testid="cited-quote"
                ref={(node) => {
                  if (node && typeof node.scrollIntoView === "function")
                    node.scrollIntoView({ block: "center" });
                }}
              >
                {quoted}
              </mark>
              {points.slice(end).join("")}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">
              The text of this document changed since it was read, so the quote
              cannot be shown in its place.
            </p>
          )}
        </div>
      )}
    </>
  );
}

/** The completeness of the whole profile: it moves as proposals are accepted. */
function Completeness() {
  const label = useId();
  const query = useQuery({
    queryKey: completenessKey,
    queryFn: ({ signal }) => getCompleteness(signal),
  });
  // The list works without it: when the profile cannot be read, the bar is left out.
  if (query.data === undefined) return null;
  const overall = query.data;
  return (
    <div className="flex flex-col gap-1.5" data-testid="review-completeness">
      <div className="flex items-baseline justify-between text-sm">
        <span id={label} className="font-medium">
          Profile completeness
        </span>
        <span className="text-muted-foreground tabular-nums">{overall}%</span>
      </div>
      <div
        role="progressbar"
        aria-labelledby={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={overall}
        className="h-2 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width]"
          style={{ width: `${String(overall)}%` }}
        />
      </div>
    </div>
  );
}

function Loaded({ review }: { review: Review }) {
  const [opened, setOpened] = useState<Citation>();
  // The last citation shown stays while the dialog closes.
  const [shown, setShown] = useState<Citation>();
  const [said, setSaid] = useState("");
  if (opened && opened !== shown) setShown(opened);
  const context: Context = {
    runId: review.run_id,
    revision: review.profile_revision,
    onOpen: setOpened,
    onSaid: setSaid,
  };
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h3 className="font-semibold tracking-tight">
          What your documents say
        </h3>
        <p className="text-sm text-muted-foreground">
          {review.counts.total === 0
            ? EMPTY_NOTE
            : `${plural(review.counts.total, "proposal", "proposals")} in ${plural(review.sections.length, "section", "sections")}. Nothing changes in your profile until you accept it.`}
        </p>
        {review.counts.total > 0 ? (
          <p
            className="text-sm text-muted-foreground tabular-nums"
            data-testid="review-left"
          >
            {review.counts.pending === 0
              ? "Every proposal is decided."
              : `${String(review.counts.pending)} left to review, ${String(review.counts.accepted)} accepted, ${String(review.counts.rejected)} rejected.`}
          </p>
        ) : null}
        {review.partial ? (
          <p className="text-sm text-muted-foreground">
            Some of your documents could not be read, so this list may be
            incomplete.
          </p>
        ) : null}
        {review.documents.length > 0 ? (
          <ul
            aria-label="Documents read"
            className="flex flex-wrap gap-x-3 text-sm text-muted-foreground"
          >
            {review.documents.map((document) => (
              <li key={document.id}>
                {document.deleted ? (
                  "Deleted"
                ) : (
                  <bdi>{document.name ?? "Document"}</bdi>
                )}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <Completeness />
      <p role="status" className="sr-only">
        {said}
      </p>
      {review.sections.map((section) => (
        <Section key={section.section} section={section} context={context} />
      ))}
      <Dialog
        open={opened !== undefined}
        onOpenChange={(open) => {
          if (!open) setOpened(undefined);
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          {shown ? (
            <QuotePreview
              key={`${shown.document_id}:${String(shown.start)}`}
              citation={shown}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function ProposalList({ runId }: { runId?: string } = {}) {
  const finished = useQuery({
    queryKey: finishedRunKey,
    queryFn: ({ signal }) => getFinishedRunId(signal),
    enabled: runId === undefined,
  });
  const id = runId ?? finished.data ?? undefined;
  const query = useQuery({
    queryKey: proposalsKey(id ?? ""),
    queryFn: ({ signal }) => getProposals(id ?? "", signal),
    enabled: id !== undefined,
  });
  if (id === undefined) return null;
  // A read that fails after a decision keeps the list on screen: only a first read replaces it.
  if (query.data !== undefined) return <Loaded review={query.data} />;
  if (query.isPending)
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading the proposals
      </p>
    );
  return (
    <div className="flex flex-col items-start gap-3">
      <p role="alert" className="text-sm text-destructive">
        The proposals could not be loaded. {failureSentence(query.error)}
      </p>
      <Button
        variant="outline"
        size="sm"
        disabled={query.isFetching}
        onClick={() => void query.refetch()}
      >
        <RotateCw aria-hidden="true" />
        Retry
      </Button>
    </div>
  );
}
