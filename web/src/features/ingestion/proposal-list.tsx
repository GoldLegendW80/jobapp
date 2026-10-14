import { useQuery } from "@tanstack/react-query";
import { FileText, LoaderCircle, RotateCw } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { failureSentence, getDocumentText } from "./documents-api";
import {
  type Citation,
  finishedRunKey,
  getFinishedRunId,
  getProposals,
  type ProposalItem,
  type ProposalSection,
  proposalsKey,
  type Review,
} from "./proposals-api";

/**
 * Step 3, third part: what a run proposes for each section of the profile, with the place in
 * the document each proposal comes from (task G12). Read only: the decisions (accept, edit,
 * reject, in bulk) come with task G13, in this file.
 *
 * `runId` is the run to show. Without it the list finds the user's latest finished run by
 * itself, and renders nothing while no run has finished.
 *
 * Everything a document wrote (a value, an excerpt) is rendered as text, and an excerpt is cut
 * to EXCERPT_SHOWN characters.
 */
export const EXCERPT_SHOWN = 300;
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
  transcribed: "Transcribed, check it",
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

function words(name: string): string {
  const spaced = name.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function plural(count: number, one: string, many: string): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}

function cut(text: string): string {
  const points = Array.from(text);
  return points.length > EXCERPT_SHOWN
    ? `${points.slice(0, EXCERPT_SHOWN).join("")}…`
    : text;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function Item({
  item,
  onOpen,
}: {
  item: ProposalItem;
  onOpen: (citation: Citation) => void;
}) {
  const title = itemTitle(item);
  const decided = STATUS_LABELS[item.status];
  return (
    <li
      data-testid="proposal"
      data-key={item.key}
      className="flex flex-col gap-2 rounded-md border p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h5 className="text-sm font-medium break-words">{title}</h5>
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
      {item.conflict.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">
            Your documents give different values. You will pick one.
          </p>
          <ol
            aria-label={`Values for ${title}`}
            className="flex flex-col gap-2"
          >
            {[
              { value: item.value, citations: item.citations },
              ...item.conflict,
            ].map((choice, index) => (
              // The values of a conflict have no identity but their place in it.
              <li
                key={index}
                className="flex flex-col gap-1 rounded-sm border p-2"
              >
                <Value
                  value={choice.value}
                  label={`Value ${String(index + 1)}`}
                />
                <Sources
                  citations={choice.citations}
                  label={`Sources of value ${String(index + 1)}`}
                  onOpen={onOpen}
                />
              </li>
            ))}
          </ol>
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
                  value={item.status === "pending" ? item.after : item.value}
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
    </li>
  );
}

function Section({
  section,
  onOpen,
}: {
  section: ProposalSection;
  onOpen: (citation: Citation) => void;
}) {
  const name = SECTION_NAMES[section.section] ?? words(section.section);
  const id = `proposals-${section.section}`;
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
      <ul className="flex flex-col gap-2">
        {section.items.map((item) => (
          <Item key={item.id} item={item} onOpen={onOpen} />
        ))}
      </ul>
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

function Loaded({ review }: { review: Review }) {
  const [opened, setOpened] = useState<Citation>();
  // The last citation shown stays while the dialog closes.
  const [shown, setShown] = useState<Citation>();
  if (opened && opened !== shown) setShown(opened);
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
      {review.sections.map((section) => (
        <Section key={section.section} section={section} onOpen={setOpened} />
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
  if (query.isError)
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
  return <Loaded review={query.data} />;
}
