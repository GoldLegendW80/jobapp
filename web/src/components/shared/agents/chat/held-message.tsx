import { Button } from "@/components/ui/button";

import { ChatTurn } from "./chat-turn";

/** The reader's message that waits to be sent, with a Cancel. */
export function HeldMessage({
  text,
  onCancel,
}: {
  text: string;
  onCancel: () => void;
}) {
  return (
    <ChatTurn
      from="held"
      data-testid="chat-held-message"
      footer={
        <>
          <span>Pending</span>
          <Button type="button" variant="ghost" size="xs" onClick={onCancel}>
            Cancel
          </Button>
        </>
      }
    >
      {text}
    </ChatTurn>
  );
}
