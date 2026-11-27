import { Hourglass, MessageCircleQuestionMark } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { LoadError } from "@/components/blocks/load-error";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { LiveViews } from "@/components/shared/browser/live-views";

import type { Application, Question } from "@/features/apply/api/apply-api";
import {
  messageClosed,
  useAgentWait,
} from "@/features/apply/api/use-agent-wait";
import {
  AFTER_SEND_WAIT,
  agentWaits,
  ASK_NOT_SHOWN,
  assistantWorks,
  boxOf,
  CODE_ASKED_AGAIN,
  CODE_BY_HAND,
  CODE_TYPED_BY_APP,
  codeAskedAgain,
  isAsk,
  kindWords,
  NO_CODE_HERE,
  showsAsk,
  stoppedLine,
  WORDS_NOT_CODE,
} from "@/features/apply/helpers/agent-wait";
import {
  clockText,
  holdLeft,
  spokenLeft,
} from "@/features/apply/helpers/full-auto-state";
import { liveStatus } from "@/features/apply/helpers/live-view";
import { describeError } from "@/lib/api";
import { AgentChat } from "./agent-chat";
import { LiveViewSlot } from "./live-view-slot";

const TICK_MS = 1000;
// The api's box: at most this many characters (router QuestionIn).
const ANSWER_MAX = 5000;
// The api's bound of a code as the person types it, separators included (router CODE_SENT_MAX).
const CODE_TYPED_MAX = 64;
// The phone's tab bar: a box in the page's flow scrolls above it when focused.
const ABOVE_TAB_BAR = "scroll-mb-(--bottom-bar)";

/**
 * The apply agent waits for the person (A25; SPEC-apply, "The apply agent waits for the person"),
 * on the application's card and in Your application: the conversation (the assistant's sentence
 * as its own words, earlier asks, answers and messages), what it asks for, how long it still
 * waits (read on the server's clock), a box to answer, and for a code a code box the app types
 * from, and the browser behind Watch the browser, opened only on a click, with Use the browser
 * myself and Let the assistant continue. Once the wait ended without the person it says why; Try
 * again and Cancel are the page's own buttons. A wait whose detail carries no ask says so in one
 * sentence. While the agent works (`works`: the caller's read of the run), a box to write to it.
 * Follows the run without a reload.
 */
export function AgentWait({
  application,
  works = false,
}: {
  application: Application;
  works?: boolean;
}) {
  const wait = useAgentWait(application, works);
  const [browserOpen, setBrowserOpen] = useState(false);
  const detail = wait.detail.data;
  if (!detail && wait.detail.isError)
    return (
      <LoadError query={wait.detail}>
        {showsAsk(application)
          ? "What the assistant asks could not be loaded."
          : "Your conversation with the assistant could not be loaded."}
      </LoadError>
    );
  if (!detail) return null;
  const question = isAsk(detail.question) ? detail.question : null;
  const chat = detail.chat ?? [];
  const writes = assistantWorks(detail, detail.review_agent === true);
  const live = question?.live === true && agentWaits(detail);
  if (!question && !writes)
    return agentWaits(detail) ? (
      <p data-testid="agent-wait" className="text-sm">
        {ASK_NOT_SHOWN}
      </p>
    ) : null;
  const box = question ? boxOf(question, chat) : null;
  return (
    <section
      aria-label={
        question ? "The assistant needs you" : "The assistant at work"
      }
      data-testid="agent-wait"
      className="flex min-w-0 flex-col gap-3"
    >
      <AgentChat chat={chat} />
      {question ? (
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
      ) : agentWaits(detail) ? (
        <p className="text-sm">{ASK_NOT_SHOWN}</p>
      ) : null}
      {live && box === "code" ? (
        <CodeBox
          again={codeAskedAgain(chat)}
          giving={wait.giving}
          error={wait.codeError}
          onGive={wait.give}
          onBrowser={() => {
            setBrowserOpen(true);
          }}
        />
      ) : null}
      {live && box ? (
        <AnswerBox
          box={box}
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
      {writes ? (
        <MessageBox
          during={live}
          pending={wait.message.isPending}
          error={wait.messageError}
          onSend={(text, done) => {
            wait.message.mutate(text, {
              onSuccess: done,
              onError: (error) => {
                if (messageClosed(error)) toast.error(describeError(error));
              },
            });
          }}
        />
      ) : null}
      {live ? (
        <WaitBrowser
          application={detail}
          question={question}
          open={browserOpen}
          onOpenChange={setBrowserOpen}
        />
      ) : null}
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

/**
 * The code box of a code question: the code goes to the api alone and the box is emptied at
 * once, whatever the api answers; nothing of it is kept here. Beside it, the way of the browser.
 */
function CodeBox({
  again,
  giving,
  error,
  onGive,
  onBrowser,
}: {
  again: boolean;
  giving: boolean;
  error: string | null;
  onGive: (code: string) => void;
  onBrowser: () => void;
}) {
  const id = useId();
  const [code, setCode] = useState("");
  return (
    <form
      aria-label="Give the code"
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const typed = code;
        setCode("");
        if (typed.trim()) onGive(typed);
      }}
    >
      {again ? <p className="text-sm">{CODE_ASKED_AGAIN}</p> : null}
      <Label htmlFor={`${id}-code`}>The code the site sent you</Label>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          id={`${id}-code`}
          aria-describedby={`${id}-typed`}
          className={`font-mono sm:max-w-64 ${ABOVE_TAB_BAR}`}
          autoComplete="one-time-code"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          maxLength={CODE_TYPED_MAX}
          value={code}
          onChange={(event) => {
            setCode(event.target.value);
          }}
        />
        <Button
          type="submit"
          className="w-full sm:w-fit"
          disabled={giving || !code.trim()}
        >
          Give the code
        </Button>
      </div>
      <p id={`${id}-typed`} className="text-sm text-muted-foreground">
        {CODE_TYPED_BY_APP}
      </p>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <p className="text-sm">{CODE_BY_HAND}</p>
        <Button
          type="button"
          variant="outline"
          className="w-full shrink-0 sm:w-fit"
          onClick={onBrowser}
        >
          Open the browser
        </Button>
      </div>
    </form>
  );
}

/** The box of words to the waiting run, or that they are in: for a code question it is for words. */
function AnswerBox({
  box,
  pending,
  onSend,
}: {
  box: NonNullable<ReturnType<typeof boxOf>>;
  pending: boolean;
  onSend: (text: string, done: () => void) => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  if (box === "sent" || box === "code_sent")
    return (
      <p role="status" className="text-sm">
        {box === "sent"
          ? "Your answer was sent. The assistant reads it in a moment."
          : "The app has your code, to type on the site. The assistant goes on in a moment."}
      </p>
    );
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
      <Label htmlFor={`${id}-answer`}>
        {box === "code"
          ? "Or words to the assistant"
          : "Your answer to the assistant"}
      </Label>
      {/* In the page's flow, not fixed, so the browser scrolls it into view when focused. */}
      <Textarea
        id={`${id}-answer`}
        aria-describedby={`${id}-no-code`}
        className={`min-h-20 ${ABOVE_TAB_BAR}`}
        maxLength={ANSWER_MAX}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
      />
      <p id={`${id}-no-code`} className="text-sm text-muted-foreground">
        {box === "code" ? WORDS_NOT_CODE : NO_CODE_HERE}
      </p>
      <Button
        type="submit"
        variant={box === "code" ? "outline" : "default"}
        className="w-full sm:w-fit"
        disabled={pending || !text.trim()}
      >
        Send the answer
      </Button>
    </form>
  );
}

/**
 * A box to write to the assistant while it works: the api takes 1 to 1,000 characters and says
 * why it refuses one; the text stays for a correction, and goes once sent.
 */
function MessageBox({
  during,
  pending,
  error,
  onSend,
}: {
  during: boolean;
  pending: boolean;
  error: string | null;
  onSend: (text: string, done: () => void) => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
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
      <Label htmlFor={`${id}-message`}>Write to the assistant</Label>
      <Textarea
        id={`${id}-message`}
        aria-describedby={`${id}-when`}
        className={`min-h-16 ${ABOVE_TAB_BAR}`}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
      />
      <p id={`${id}-when`} className="text-sm text-muted-foreground">
        {during
          ? "It reads your message once this wait ends. To answer what it asks, use the boxes above."
          : "It reads your message with its next step. Up to 1,000 characters."}
      </p>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button
        type="submit"
        variant="outline"
        className="w-full sm:w-fit"
        disabled={pending || !text.trim()}
      >
        Send the message
      </Button>
    </form>
  );
}

/** The browser the run waits in, behind Watch the browser: closed until the person opens it. */
function WaitBrowser({
  application,
  question,
  open,
  onOpenChange,
}: {
  application: Application;
  question: Question;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const place = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) place.current?.scrollIntoView({ block: "nearest" });
  }, [open]);
  return (
    <div ref={place} className={ABOVE_TAB_BAR}>
      <LiveViews
        label="The browser the assistant waits in"
        open={open}
        onOpenChange={onOpenChange}
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
          assistantWaits={agentWaits(application)}
        />
      </LiveViews>
    </div>
  );
}
