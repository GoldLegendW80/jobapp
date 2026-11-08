import { Check, Copy } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { LoadError } from "@/components/blocks/load-error";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { loadFailureSentence } from "@/features/ingestion/api/documents-api";
import { usePrompt } from "@/features/ingestion/api/use-prompt";

const ASSISTANTS = [
  { id: "claude_code", name: "Claude Code", command: "claude" },
  { id: "codex", name: "Codex", command: "codex" },
] as const;
type AssistantId = (typeof ASSISTANTS)[number]["id"];

const isAssistantId = (value: string): value is AssistantId =>
  ASSISTANTS.some((assistant) => assistant.id === value);

/** What the last press of Copy did, and for which assistant's prompt. */
interface CopyResult {
  assistant: AssistantId;
  outcome: "copied" | "failed";
}

/**
 * The prompt the person copies and runs with Claude Code or Codex on their own computer, with the
 * steps to run it: that assistant looks through the home folder and answers with the profile.
 * `addIt` ends the last step: where, on this page, the person pastes that answer; `flat` leaves
 * out the card, inside one already. The app never reads the computer: this only shows text (GET
 * /api/ingestion/prompt).
 */
export function AssistantPrompt({
  addIt,
  flat = false,
}: {
  addIt: ReactNode;
  flat?: boolean;
}) {
  const query = usePrompt();
  const [assistant, setAssistant] = useState<AssistantId>("claude_code");
  const [copy, setCopy] = useState<CopyResult>();
  const text = useRef<HTMLTextAreaElement>(null);

  const onCopy = async (id: AssistantId, prompt: string) => {
    try {
      // Missing outside a secure context (plain http on another host than localhost).
      const clipboard = navigator.clipboard as Clipboard | undefined;
      if (!clipboard) throw new Error("no clipboard");
      await clipboard.writeText(prompt);
      setCopy({ assistant: id, outcome: "copied" });
      toast.success("Prompt copied.");
    } catch {
      setCopy({ assistant: id, outcome: "failed" });
      // The way that always works: the whole text selected, the user copies it.
      text.current?.focus();
      text.current?.select();
    }
  };

  if (query.isPending)
    return (
      <div role="status" aria-live="polite">
        <span className="sr-only">Loading the prompt</span>
        <Skeleton aria-hidden="true" className="h-96" />
      </div>
    );
  if (query.isError)
    return (
      <LoadError query={query} reason={loadFailureSentence(query.error)}>
        The prompt could not be loaded.
      </LoadError>
    );
  const tabs = (
    <Tabs
      value={assistant}
      onValueChange={(value) => {
        if (isAssistantId(value)) setAssistant(value);
      }}
    >
      <TabsList aria-label="Your coding assistant">
        {ASSISTANTS.map(({ id, name }) => (
          <TabsTrigger key={id} value={id} className="flex-1">
            {name}
          </TabsTrigger>
        ))}
      </TabsList>
      {ASSISTANTS.map(({ id, name, command }) => {
        const prompt = query.data[id];
        const outcome = copy?.assistant === id ? copy.outcome : undefined;
        return (
          <TabsContent key={id} value={id} className="flex flex-col gap-4">
            <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-sm">
              <li>Copy the prompt below.</li>
              <li>
                Open a terminal in your home folder and start {name} by typing{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                  {command}
                </code>
                .
              </li>
              <li>
                Paste the prompt and send it. It looks through your home folder
                for everything about your work, which can take a while.
              </li>
              <li>Read its answer, copy it whole, then {addIt}.</li>
            </ol>

            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Label htmlFor={`prompt-${id}`}>Prompt for {name}</Label>
                <Button
                  type="button"
                  onClick={() => void onCopy(id, prompt)}
                  aria-describedby={`prompt-copy-${id}`}
                >
                  {outcome === "copied" ? (
                    <Check aria-hidden="true" />
                  ) : (
                    <Copy aria-hidden="true" />
                  )}
                  {outcome === "copied" ? "Copied" : "Copy the prompt"}
                </Button>
              </div>
              <Textarea
                ref={text}
                id={`prompt-${id}`}
                readOnly
                rows={14}
                spellCheck={false}
                value={prompt}
                aria-describedby={`prompt-version-${id}`}
                // The prompt is long: its 14 rows scroll, they do not grow with the text.
                className="resize-y bg-muted/40 p-3 font-mono text-xs leading-relaxed field-sizing-fixed md:text-xs dark:bg-muted/40"
              />
              <p
                id={`prompt-copy-${id}`}
                role="status"
                className={
                  outcome === "failed"
                    ? "text-sm font-medium text-destructive"
                    : "text-sm text-muted-foreground"
                }
              >
                {outcome === "failed"
                  ? "This browser blocked copying. The prompt is selected: copy it as you usually do, or select the text and copy it."
                  : outcome === "copied"
                    ? "The prompt is on your clipboard."
                    : ""}
              </p>
              <p
                id={`prompt-version-${id}`}
                className="text-xs text-muted-foreground"
              >
                Prompt version {query.data.version}. Read it before you run it:
                it lists what the assistant may read and what it must leave
                alone.
              </p>
            </div>
          </TabsContent>
        );
      })}
    </Tabs>
  );
  return flat ? (
    tabs
  ) : (
    <Card>
      <CardContent>{tabs}</CardContent>
    </Card>
  );
}
