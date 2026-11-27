import { Hourglass, MessageCircleQuestionMark } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { LiveViews } from "@/components/shared/browser/live-views";

import type { Application, Question } from "@/features/apply/api/apply-api";
import { useAgentWait } from "@/features/apply/api/use-agent-wait";
import {
  AFTER_SEND_WAIT,
  agentWaits,
  boxOf,
  CODE_IN_BROWSER,
  isAsk,
  kindWords,
  stoppedLine,
} from "@/features/apply/helpers/agent-wait";
import {
  clockText,
  holdLeft,
  spokenLeft,
} from "@/features/apply/helpers/full-auto-state";
import { liveStatus } from "@/features/apply/helpers/live-view";
import { AgentChat } from "./agent-chat";
import { LiveViewSlot } from "./live-view-slot";

const TICK_MS = 1000;
// The api's box: at most this many characters (router QuestionIn).
const ANSWER_MAX = 5000;

/**
 * The apply agent waits for the person (A25; SPEC-apply, "The apply agent waits for the person"),
 * on the application's card and in Your application: the conversation (the assistant's sentence
 * as its own words, earlier asks and answers), what it asks for, how long it still waits (read on
 * the server's clock), a box to answer (none for a code, typed in the browser), and the browser
 * behind Watch the browser, opened only on a click, with Use the browser myself and Let the
 * assistant continue. Once the wait ended without the person it says why; Try again and Cancel
 * are the page's own buttons. Follows the run without a reload.
 */
export function AgentWait({ application }: { application: Application }) {
  const wait = useAgentWait(application);
  const detail = wait.detail.data;
  const question = isAsk(detail?.question) ? detail?.question : null;
  const chat = detail?.chat ?? [];
  if (!detail || !question) return null;
  const live = question.live === true && agentWaits(detail);
  return (
    <section
      aria-label="The assistant needs you"
      data-testid="agent-wait"
      className="flex min-w-0 flex-col gap-3"
    >
      <AgentChat chat={chat} />
      <Alert variant={live ? "warning" : "default"} role="note">
        {live ? (
          <MessageCircleQuestionMark aria-hidden="true" />
        ) : (
          <Hourglass aria-hidden="true" />
        )}
        <AlertTitle>
          {live ? "The assistant needs you" : "The assistant stopped waiting"}
        </AlertTitle>
        <AlertDescription className="gap-1 text-foreground">
          <p>It asks for {kindWords(question.kind)}.</p>
          {live ? (
            <TimeLeft until={question.until} offset={wait.offset} />
          ) : (
            <p>{stoppedLine(chat)}</p>
          )}
          {question.after_send ? <p>{AFTER_SEND_WAIT}</p> : null}
        </AlertDescription>
      </Alert>
      {live ? (
        <AnswerBox
          box={boxOf(question, chat)}
          pending={wait.answer.isPending}
          onSend={(text, done) => {
            wait.answer.mutate(text, { onSuccess: done });
          }}
        />
      ) : null}
      {wait.answerError ? (
        <p role="alert" className="text-sm text-destructive">
          {wait.answerError}
        </p>
      ) : null}
      {live ? <WaitBrowser application={detail} question={question} /> : null}
    </section>
  );
}

/** "It waits 12:04 more for you": the time left of the wait, ticking on the server's clock. */
function TimeLeft({
  until,
  offset,
}: {
  until: string | null | undefined;
  offset: number;
}) {
  const [pageNow, setPageNow] = useState(() => Date.now());
  const left = until ? holdLeft(until, offset, pageNow) : 0;
  const running = left > 0;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      setPageNow(Date.now());
    }, TICK_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [running]);
  if (!until) return <p>It waits for you a little while, then stops.</p>;
  return running ? (
    <p>
      It waits for you{" "}
      <span
        role="timer"
        aria-label={spokenLeft(left)}
        className="font-semibold tabular-nums"
      >
        {clockText(left)}
      </span>{" "}
      more, with the browser held, then stops.
    </p>
  ) : (
    <p role="status">The time to answer is up: the assistant stops now.</p>
  );
}

/** The box to answer the waiting run, or why there is none: a code goes in the browser, an answer is in. */
function AnswerBox({
  box,
  pending,
  onSend,
}: {
  box: ReturnType<typeof boxOf>;
  pending: boolean;
  onSend: (text: string, done: () => void) => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  if (box === "browser") return <p className="text-sm">{CODE_IN_BROWSER}</p>;
  if (box === "sent")
    return (
      <p role="status" className="text-sm">
        Your answer was sent. The assistant reads it in a moment.
      </p>
    );
  if (box !== "answer") return null;
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (text.trim())
          onSend(text, () => {
            setText("");
          });
      }}
    >
      <Label htmlFor={`${id}-answer`}>Your answer to the assistant</Label>
      {/* In the page's flow, never fixed: a phone scrolls it above its keyboard and the tab bar. */}
      <Textarea
        id={`${id}-answer`}
        className="min-h-20 scroll-mb-(--bottom-bar)"
        maxLength={ANSWER_MAX}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
      />
      <Button
        type="submit"
        className="w-full sm:w-fit"
        disabled={pending || !text.trim()}
      >
        Send the answer
      </Button>
    </form>
  );
}

/** The browser the run waits in, behind Watch the browser: closed until the person opens it. */
function WaitBrowser({
  application,
  question,
}: {
  application: Application;
  question: Question;
}) {
  const [open, setOpen] = useState(false);
  return (
    <LiveViews
      label="The browser the assistant waits in"
      open={open}
      onOpenChange={setOpen}
    >
      <LiveViewSlot
        idle={
          question.host
            ? `The browser on ${question.host} shows here once it is ready.`
            : "The browser shows here once it is ready."
        }
        leaseOf={{ application: application.id }}
        status={(purpose) => liveStatus(purpose, application)}
        namedOnly
      />
    </LiveViews>
  );
}
