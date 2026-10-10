/**
 * Chat about one job (agents A4): a Chat button on job detail opens a side panel (a full screen
 * sheet on a phone) holding the conversation with the chat agent. The conversation lives in the
 * database, one thread per job: it survives a reload, and so does the panel being open (`?chat`
 * in the URL). Each message starts a run of the chat agent, whose answer streams in live through
 * the run stream (useRunStream) until the stored answer replaces it.
 *
 * Messages are plain text (no markdown is rendered). While an answer is pending the Send button
 * is disabled (the server would answer 409); the draft can still be written. Screen readers hear
 * each new answer once, through one polite live region, not every streamed step.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleAlert,
  LoaderCircle,
  MessagesSquare,
  SendHorizontal,
  Trash2,
} from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type { JobDetail } from "@/features/jobs/api";
import { describeError, errorDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

import { getModelCredential, modelCredentialQueryKey } from "./api";
import {
  MESSAGE_MAX,
  busyRunId,
  deleteThread,
  findJobThread,
  getThread,
  jobThreadKey,
  openJobThread,
  sendMessage,
  threadKey,
  type ChatMessage,
  type ThreadDetail,
} from "./chat-api";
import { FINAL_STATUSES, runsKey, type RunStep } from "./runs-api";
import { useRunStream } from "./use-run-stream";

const CHAT_PARAM = "chat";

const EXAMPLE_PROMPTS = [
  "Does this role fit my profile?",
  "What should I stress in my application?",
  "What should I check before applying?",
];

/** The Chat button and its panel. The panel's open state is kept in the URL (`?chat=open`). */
export function JobChat({ job }: { job: JobDetail }) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const open = params.get(CHAT_PARAM) === "open";
  const setOpen = (next: boolean) => {
    const updated = new URLSearchParams(params);
    if (next) updated.set(CHAT_PARAM, "open");
    else updated.delete(CHAT_PARAM);
    // Keep the location state: it holds where "Back to jobs" goes.
    setParams(updated, { replace: true, state: location.state as unknown });
  };
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm" className="shrink-0">
          <MessagesSquare aria-hidden="true" />
          Chat
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        data-testid="chat-panel"
        className="w-full max-w-none gap-0 sm:w-[28rem] sm:max-w-md"
      >
        <SheetHeader className="border-b">
          <SheetTitle>Chat about this job</SheetTitle>
          <SheetDescription className="truncate">
            {job.title} at {job.company}
          </SheetDescription>
        </SheetHeader>
        {open ? <ChatBody job={job} /> : null}
      </SheetContent>
    </Sheet>
  );
}

/** What the agent is doing, from its newest step, while no answer text has arrived. */
function activity(steps: RunStep[], queued: boolean): string {
  if (queued) return "Waiting for a free agent";
  const last = steps.at(-1);
  if (last?.kind === "tool_call") {
    const name = typeof last.data.name === "string" ? last.data.name : "";
    if (name.endsWith("get_job")) return "Reading the posting";
    if (name.endsWith("get_profile_section")) return "Reading your profile";
  }
  return "Thinking";
}

/** The answer being written: the newest text the run streamed, or what it is doing. */
function LiveReply({ runId, onEnd }: { runId: string; onEnd: () => void }) {
  const { steps, status } = useRunStream(runId);
  const ended = status !== undefined && FINAL_STATUSES.has(status);
  const endRef = useRef(onEnd);
  useEffect(() => {
    endRef.current = onEnd;
  });
  useEffect(() => {
    if (ended) endRef.current();
  }, [ended]);
  if (ended) return null;
  const texts = steps.filter(
    (step) => step.kind === "text" && typeof step.data.text === "string",
  );
  const text = texts.at(-1)?.data.text as string | undefined;
  return (
    <li
      data-testid="chat-live-reply"
      className="flex flex-col items-start gap-1"
      aria-busy="true"
    >
      <span className="sr-only">The agent is answering:</span>
      <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-3 py-2 text-sm break-words whitespace-pre-wrap">
        {text ?? (
          <span className="text-muted-foreground">
            {activity(steps, status === "queued")}
          </span>
        )}
      </div>
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <LoaderCircle aria-hidden="true" className="size-3 animate-spin" />
        {text ? activity(steps, false) : "Answering"}
      </span>
    </li>
  );
}

function unanswered(message: ChatMessage, messages: ChatMessage[]): boolean {
  if (message.role !== "user") return false;
  if (message.run_status !== "failed" && message.run_status !== "cancelled")
    return false;
  return !messages.some(
    (other) => other.role === "assistant" && other.run_id === message.run_id,
  );
}

function MessageItem({
  message,
  messages,
}: {
  message: ChatMessage;
  messages: ChatMessage[];
}) {
  const mine = message.role === "user";
  const failed = unanswered(message, messages);
  return (
    <li
      data-testid="chat-message"
      data-role={message.role}
      className={cn("flex flex-col gap-1", mine ? "items-end" : "items-start")}
    >
      <span className="sr-only">{mine ? "You:" : "Agent:"}</span>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-3 py-2 text-sm break-words whitespace-pre-wrap",
          mine
            ? "rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-bl-sm bg-muted",
        )}
      >
        {message.text || (
          <span className="text-muted-foreground">The agent gave no text.</span>
        )}
      </div>
      {failed ? (
        <p className="inline-flex items-center gap-1 text-xs text-destructive">
          <CircleAlert aria-hidden="true" className="size-3.5" />
          No answer:{" "}
          {message.error ??
            (message.run_status === "cancelled"
              ? "the answer was stopped."
              : "the agent failed.")}
        </p>
      ) : null}
    </li>
  );
}

function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="flex flex-col gap-4 py-6" data-testid="chat-empty">
      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium">Ask anything about this posting.</p>
        <p className="text-sm text-muted-foreground">
          The agent reads the posting and your profile to answer. It changes
          nothing, and it only knows what they say.
        </p>
      </div>
      <ul aria-label="Example questions" className="flex flex-col gap-2">
        {EXAMPLE_PROMPTS.map((prompt) => (
          <li key={prompt}>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-auto w-full justify-start py-2 text-left whitespace-normal"
              onClick={() => {
                onPick(prompt);
              }}
            >
              {prompt}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function newestAnswer(detail: ThreadDetail | undefined): ChatMessage | null {
  if (!detail) return null;
  return (
    [...detail.messages].reverse().find((m) => m.role === "assistant") ?? null
  );
}

function ChatBody({ job }: { job: JobDetail }) {
  const client = useQueryClient();
  const ids = { composer: useId(), hint: useId() };
  const composer = useRef<HTMLTextAreaElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const announced = useRef<number | null | undefined>(undefined);

  const credential = useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
  });
  const found = useQuery({
    queryKey: jobThreadKey(job.id),
    queryFn: ({ signal }) => findJobThread(job.id, signal),
  });
  const threadId = found.data?.id ?? null;
  const thread = useQuery({
    queryKey: threadKey(threadId ?? "none"),
    queryFn: ({ signal }) => getThread(threadId ?? "", signal),
    enabled: threadId !== null,
  });
  const detail = threadId ? thread.data : undefined;
  const messages = detail?.messages ?? [];
  const activeRun = detail?.active_run_id ?? null;

  const refresh = () => {
    if (threadId)
      void client.invalidateQueries({ queryKey: threadKey(threadId) });
    void client.invalidateQueries({ queryKey: runsKey, exact: true });
  };

  // One announcement per new answer (or failure), never the answers already there.
  useEffect(() => {
    if (!detail) return;
    const answer = newestAnswer(detail);
    const lastFailed = detail.messages
      .filter((m) => unanswered(m, detail.messages))
      .at(-1);
    const key = Math.max(answer?.id ?? 0, lastFailed?.id ?? 0) || null;
    if (announced.current === undefined) {
      announced.current = key;
      return;
    }
    if (key !== null && key !== announced.current) {
      announced.current = key;
      setAnnouncement(
        lastFailed && lastFailed.id === key
          ? `No answer: ${lastFailed.error ?? "the agent failed."}`
          : `Answer: ${answer?.text ?? ""}`,
      );
    }
  }, [detail]);

  // Keep the newest message in view.
  const liveSteps = activeRun ?? "";
  useEffect(() => {
    const node = list.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, liveSteps]);

  const send = useMutation({
    mutationFn: async (text: string) => {
      let target = found.data ?? null;
      if (target === null) {
        target = await openJobThread(job.id);
        client.setQueryData(jobThreadKey(job.id), target);
      }
      return { target, sent: await sendMessage(target.id, text) };
    },
    onSuccess: async ({ target, sent }) => {
      setDraft("");
      const id = target.id;
      // A read of the thread may be in flight, started before the message was stored (on the
      // first message it starts as soon as the thread exists, next to the send). Its answer is
      // older than the send: drop it, so it can neither hide the message nor be the only read.
      await client.cancelQueries({ queryKey: threadKey(id) });
      client.setQueryData<ThreadDetail>(threadKey(id), (old) => {
        const known = old ?? {
          ...target,
          messages: [],
          messages_truncated: false,
          active_run_id: null,
        };
        return {
          ...known,
          messages: [
            ...known.messages.filter((m) => m.id !== sent.message.id),
            sent.message,
          ],
          active_run_id: sent.run.id,
        };
      });
      void client.invalidateQueries({ queryKey: threadKey(id) });
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
    },
    onError: (error) => {
      if (busyRunId(error)) {
        toast.info("The previous message is still being answered.");
      } else {
        toast.error(
          `The message was not sent. ${errorDetail(error) ?? describeError(error)}`,
        );
      }
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteThread(threadId ?? ""),
    onSuccess: () => {
      setConfirming(false);
      client.setQueryData(jobThreadKey(job.id), null);
      client.removeQueries({ queryKey: threadKey(threadId ?? "") });
      announced.current = undefined;
      toast.info("Conversation deleted.");
    },
    onError: (error) => {
      toast.error(errorDetail(error) ?? describeError(error));
    },
  });

  const noCredential = credential.isSuccess && credential.data === null;
  const loading = found.isPending || (threadId !== null && thread.isPending);
  const failedToLoad = found.isError || thread.isError;
  const busy = activeRun !== null || send.isPending;
  const text = draft.trim();
  const canSend = !busy && !noCredential && !loading && text.length > 0;

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSend) return;
    send.mutate(text);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* The new answer only, once: the list itself is not live. */}
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      {threadId ? (
        <div className="flex items-center justify-end gap-2 border-b px-4 py-2">
          {confirming ? (
            <>
              <span className="mr-auto text-sm">Delete this conversation?</span>
              <Button
                variant="destructive"
                size="sm"
                disabled={remove.isPending}
                aria-busy={remove.isPending}
                onClick={() => {
                  remove.mutate();
                }}
              >
                Delete
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setConfirming(false);
                }}
              >
                Keep
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setConfirming(true);
              }}
            >
              <Trash2 aria-hidden="true" />
              Delete conversation
            </Button>
          )}
        </div>
      ) : null}
      <div ref={list} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {loading ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Loading the conversation
          </p>
        ) : failedToLoad ? (
          <p className="text-sm text-destructive">
            The conversation could not be loaded.{" "}
            {describeError(found.error ?? thread.error)}
          </p>
        ) : messages.length === 0 && !busy ? (
          <EmptyState
            onPick={(prompt) => {
              setDraft(prompt);
              composer.current?.focus();
            }}
          />
        ) : (
          <>
            {detail?.messages_truncated ? (
              <p className="pb-3 text-center text-xs text-muted-foreground">
                Older messages are not shown.
              </p>
            ) : null}
            <ol aria-label="Messages" className="flex flex-col gap-4">
              {messages.map((message) => (
                <MessageItem
                  key={message.id}
                  message={message}
                  messages={messages}
                />
              ))}
              {activeRun ? (
                <LiveReply key={activeRun} runId={activeRun} onEnd={refresh} />
              ) : null}
            </ol>
          </>
        )}
      </div>
      <form
        onSubmit={submit}
        className="flex flex-col gap-2 border-t p-4"
        aria-label="Send a message"
      >
        {noCredential ? (
          <p className="text-sm text-muted-foreground">
            Agents run with your own model credential, and none is saved yet.{" "}
            <Link to="/account" className="underline underline-offset-4">
              Add one on the Account page
            </Link>
            .
          </p>
        ) : null}
        <label htmlFor={ids.composer} className="sr-only">
          Message
        </label>
        <div className="flex items-end gap-2">
          <Textarea
            id={ids.composer}
            ref={composer}
            value={draft}
            maxLength={MESSAGE_MAX}
            rows={2}
            placeholder="Ask about this job"
            className="max-h-40 min-h-11 resize-none"
            aria-describedby={ids.hint}
            disabled={noCredential}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
            onKeyDown={onKeyDown}
          />
          <Button
            type="submit"
            size="icon"
            className="size-11 shrink-0"
            disabled={!canSend}
            aria-busy={send.isPending}
          >
            {send.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : (
              <SendHorizontal aria-hidden="true" />
            )}
            <span className="sr-only">Send</span>
          </Button>
        </div>
        <p id={ids.hint} className="text-xs text-muted-foreground">
          {busy
            ? "Wait for the answer before sending the next message."
            : "Enter sends, Shift and Enter adds a line."}
        </p>
      </form>
    </div>
  );
}
