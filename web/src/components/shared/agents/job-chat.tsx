/**
 * Chat about one job (agents A4): a Chat button on job detail opens a side panel (a full screen
 * sheet on a phone) holding the conversation with the chat agent. The conversation lives in the
 * database, one thread per job: it survives a reload, and so does the panel being open (`?chat`
 * in the URL). Each message starts a run of the chat agent, whose answer appears while it is
 * written (issue #33: useRunStream's `live` text, a few updates a second) until the stored answer
 * replaces it, in the same bubble markup and the same render, so nothing flickers or jumps.
 *
 * Messages are plain text (no markdown is rendered). While an answer is pending the Send button
 * is disabled (the server would answer 409); the draft can still be written. Screen readers hear
 * each new answer once and changes to the composer hint, through polite live regions, never the
 * pieces of a growing answer (its bubble is aria-busy and not live). The thread (MessageScroller)
 * follows the newest text until the reader scrolls up, and holds a question just sent in view
 * while it is answered. Nothing here moves under prefers-reduced-motion.
 */
import { MessagesSquare } from "lucide-react";
import { useLocation, useSearchParams } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import type { JobDetail } from "@/features/jobs/api/jobs-api";

import { ChatConversation } from "./chat/chat-conversation";

const CHAT_PARAM = "chat";

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
        <Button variant="outline" className="shrink-0">
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
        {open ? <ChatConversation job={job} /> : null}
      </SheetContent>
    </Sheet>
  );
}
