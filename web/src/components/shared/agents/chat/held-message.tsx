import { Button } from "@/components/ui/button";

import { ChatBubble } from "./chat-bubble";

/** The reader's message that waits to be sent, with a Cancel. */
export function HeldMessage({
  text,
  onCancel,
}: {
  text: string;
  onCancel: () => void;
}) {
  return (
    <div
      data-testid="chat-held-message"
      className="flex flex-col items-end gap-1"
    >
      <span className="sr-only">You:</span>
      <ChatBubble from="held">{text}</ChatBubble>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>Pending</span>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
