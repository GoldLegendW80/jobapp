import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

/**
 * A command to type in a terminal, inline in a sentence, with a small Copy button after it.
 * Where the browser blocks the clipboard (plain http on another host), the toast says so.
 */
export function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      const clipboard = navigator.clipboard as Clipboard | undefined;
      if (!clipboard) throw new Error("no clipboard");
      await clipboard.writeText(command);
      setCopied(true);
      toast.success("Command copied.");
    } catch {
      toast.error(
        "This browser blocked copying: select the command and copy it.",
      );
    }
  };

  return (
    <span className="inline-flex items-center gap-0.5 align-middle">
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">
        {command}
      </code>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label={`Copy ${command}`}
        onClick={() => void copy()}
      >
        {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      </Button>
    </span>
  );
}
