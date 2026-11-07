import type { ComponentProps, ReactNode } from "react";

import { Bubble, BubbleContent } from "@/components/ui/bubble";
import {
  Message,
  MessageContent,
  MessageFooter,
} from "@/components/ui/message";
import { cn } from "@/lib/utils";

const LOOK = {
  user: { align: "end", variant: "default", label: "You:" },
  agent: { align: "start", variant: "muted", label: "Agent:" },
  /** a message of the reader that waits for its turn */
  held: { align: "end", variant: "outline", label: "You:" },
} as const;

/**
 * One turn of any chat of the app: who wrote it for screen readers, its bubble, and a line under
 * it (why there is no answer, what the agent is doing, Pending, a version, Stop). The answer being
 * written and the stored one are the same turn, so nothing moves when one replaces the other.
 */
export function ChatTurn({
  from,
  label,
  footer,
  contentTestId,
  children,
  ...props
}: Omit<ComponentProps<typeof Message>, "align"> & {
  from: keyof typeof LOOK;
  /** What a screen reader hears before the text: "You:" or "Agent:" by default, "Assistant:" in
   * a document's chat. */
  label?: string;
  footer?: ReactNode;
  contentTestId?: string;
}) {
  const look = LOOK[from];
  return (
    <Message align={look.align} {...props}>
      <MessageContent className="gap-1">
        <span className="sr-only">{label ?? look.label}</span>
        <Bubble align={look.align} variant={look.variant}>
          <BubbleContent
            data-testid={contentTestId}
            className={cn(
              "whitespace-pre-wrap",
              from === "held" && "border-dashed",
            )}
          >
            {children}
          </BubbleContent>
        </Bubble>
        {footer ? (
          <MessageFooter className="gap-2">{footer}</MessageFooter>
        ) : null}
      </MessageContent>
    </Message>
  );
}
