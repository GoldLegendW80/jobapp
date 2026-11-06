import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const FROM = {
  user: "rounded-br-sm bg-primary text-primary-foreground",
  agent: "rounded-bl-sm bg-muted",
  /** a message of the reader that waits for its turn */
  held: "rounded-br-sm border border-dashed bg-muted",
};

/** One bubble for a stored message and for the answer being written: the same box. */
export function ChatBubble({
  from,
  className,
  ...props
}: ComponentProps<"div"> & { from: keyof typeof FROM }) {
  return (
    <div
      className={cn(
        "max-w-[85%] rounded-2xl px-3 py-2 text-sm break-words whitespace-pre-wrap",
        FROM[from],
        className,
      )}
      {...props}
    />
  );
}
