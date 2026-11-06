/**
 * The hiring panel of a document (documents D29; SPEC-documents.md, "The hiring panel"):
 * adapted from hiring-panel-skill by Alex Chen (MIT). `PanelPanel` is the document page's tab
 * "Panel": one row per reviewer (its verdict and the line that hurt most), the numbered mistakes
 * and how each was fixed, the screen before and after, and the questions, each with a link to the
 * profile section that would hold the fact. When the document was strong it says so and names the
 * reviewer that came closest to rejecting it. `PanelLine` is the one line of the job page's card.
 * No dashes as separators.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { describeError } from "@/lib/api";

import type { PanelProps } from "./document-view";
import { getPanel, panelKey, type Panel } from "./documents-api";
import { panelSentence } from "./panel-text";

export const available = true;
/** How often a running panel is read again. */
export const PANEL_POLL_MS = 5_000;

/** Where each kind of fact lives in the profile. */
const SECTION_PATH: Record<Panel["questions"][number]["section"], string> = {
  facts: "/profile?tab=facts",
  experience: "/profile?tab=experience",
  skills: "/profile?tab=skills",
  answers: "/profile?tab=answers",
};
const SECTION_LABEL: Record<Panel["questions"][number]["section"], string> = {
  facts: "Facts",
  experience: "Experience",
  skills: "Skills",
  answers: "Answers",
};
/** What failed, in the person's words. */
const FAILED: Record<string, string> = {
  reviews: "No reviewer could read it",
  merge: "The list of mistakes could not be made",
  rebuild: "The rewrite did not finish",
  rebuild_busy: "The rewrite could not start while another change was running",
  posting_applied:
    "No rewrite: the posting was sent meanwhile, so the version sent stays",
  screen_after: "The screen could not read the new version",
  panel_error: "The panel stopped before its end",
  not_queued: "The panel could not start",
};
const GOOD = new Set(["PASS", "INTERVIEW", "HIRE"]);

function usePanel(documentId: string | undefined) {
  return useQuery({
    queryKey: panelKey(documentId ?? ""),
    queryFn: ({ signal }) => getPanel(documentId as string, signal),
    enabled: documentId !== undefined,
    refetchInterval: (query) =>
      query.state.data?.panel?.status === "running" ? PANEL_POLL_MS : false,
  });
}

export function PanelLine({ documentId }: { documentId: string | undefined }) {
  const query = usePanel(documentId);
  const panel = query.data?.panel ?? null;
  if (panel === null) return null;
  return (
    <p className="text-sm text-muted-foreground" data-testid="panel-line">
      {panelSentence(panel)}
    </p>
  );
}

function Verdict({ value }: { value: string | null }) {
  if (value === null)
    return <span className="text-muted-foreground">No answer</span>;
  return (
    <Badge variant={GOOD.has(value) ? "secondary" : "outline"}>{value}</Badge>
  );
}

export function PanelPanel({ document }: PanelProps) {
  const query = usePanel(document.id);
  if (query.isPending)
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner /> Reading the hiring panel
      </p>
    );
  if (query.isError)
    return (
      <Alert variant="destructive">
        <AlertTitle>The hiring panel could not be read</AlertTitle>
        <AlertDescription>{describeError(query.error)}</AlertDescription>
      </Alert>
    );
  const panel = query.data.panel;
  if (panel === null)
    return (
      <p className="text-sm text-muted-foreground" data-testid="panel-none">
        No hiring panel has read this document yet. It reads each CV and letter
        once it is written.
      </p>
    );
  return (
    <div className="flex flex-col gap-4" data-testid="panel-view">
      <p className="text-sm">{panelSentence(panel)}.</p>
      {panel.status === "running" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> The reviewers are reading version {panel.reviewed_version}
          .
        </p>
      ) : null}
      {panel.strong ? (
        <Alert data-testid="panel-strong">
          <AlertTitle>A strong document</AlertTitle>
          <AlertDescription>
            The reviewers found nothing to fix.
            {panel.closest !== null
              ? ` ${panel.closest.label} came closest to rejecting it: ${panel.closest.why}`
              : null}
          </AlertDescription>
        </Alert>
      ) : null}
      {panel.failed.length > 0 ? (
        <Alert data-testid="panel-failed">
          <AlertTitle>The first version stays</AlertTitle>
          <AlertDescription>
            {panel.failed.map((code) => FAILED[code] ?? code).join(". ")}.
          </AlertDescription>
        </Alert>
      ) : null}

      <section
        className="flex flex-col gap-2"
        aria-labelledby="panel-reviewers"
      >
        <h3 id="panel-reviewers" className="text-sm font-semibold">
          The seven reviewers
        </h3>
        <Table aria-labelledby="panel-reviewers">
          <TableHeader>
            <TableRow>
              <TableHead>Reviewer</TableHead>
              <TableHead>Verdict</TableHead>
              <TableHead className="hidden sm:table-cell">
                The line that hurt most
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {panel.reviews.map((review) => (
              <TableRow key={review.role}>
                <TableCell className="whitespace-normal">
                  {review.label}
                  {review.line !== null ? (
                    <span className="mt-1 block text-xs text-muted-foreground break-words sm:hidden">
                      “{review.line}”
                    </span>
                  ) : null}
                </TableCell>
                <TableCell>
                  <Verdict value={review.verdict} />
                </TableCell>
                <TableCell className="hidden whitespace-normal break-words sm:table-cell">
                  {review.line !== null ? `“${review.line}”` : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      {panel.mistakes.length > 0 ? (
        <section
          className="flex flex-col gap-2"
          aria-labelledby="panel-mistakes"
        >
          <h3 id="panel-mistakes" className="text-sm font-semibold">
            What was wrong, and how it was fixed
          </h3>
          <ol
            className="flex flex-col gap-3 text-sm"
            aria-labelledby="panel-mistakes"
          >
            {panel.mistakes.map((mistake) => (
              <li key={mistake.number} className="flex flex-col gap-1">
                <span className="font-medium break-words">
                  {mistake.number}. “{mistake.quote}”
                </span>
                <span className="text-muted-foreground">{mistake.why}</span>
                {mistake.fix ? (
                  <span>
                    {panel.rebuilt_version !== null ? "Fixed" : "To fix"}:{" "}
                    {mistake.fix}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {panel.screen_before !== null ? (
        <p className="text-sm" data-testid="panel-screens">
          The automated screen: {panel.screen_before} before
          {panel.screen_after !== null
            ? `, ${panel.screen_after} after the rewrite`
            : ""}
          .
          {panel.rebuilt_version !== null
            ? ` Version ${String(panel.rebuilt_version)} is the rewrite; version ${String(panel.reviewed_version)} stays in the history.`
            : ""}
        </p>
      ) : null}

      {panel.questions.length > 0 ? (
        <section
          className="flex flex-col gap-2"
          aria-labelledby="panel-questions"
        >
          <h3 id="panel-questions" className="text-sm font-semibold">
            Facts the panel asks for
          </h3>
          <ul
            className="flex flex-col gap-2 text-sm"
            aria-labelledby="panel-questions"
          >
            {panel.questions.map((question) => (
              <li key={question.question} className="break-words">
                {question.question}{" "}
                <Link
                  to={SECTION_PATH[question.section]}
                  className="font-medium underline underline-offset-4"
                >
                  Add it in {SECTION_LABEL[question.section]}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <p className="text-xs text-muted-foreground">
        A structured second opinion by language models, adapted from
        hiring-panel-skill by Alex Chen. It does not guarantee an interview.
      </p>
    </div>
  );
}
