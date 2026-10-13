import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  Copy,
  Info,
  LoaderCircle,
  RotateCw,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { loadFailureSentence } from "./documents-api";
import { getPrompt, promptKey } from "./prompt-api";
import type { StepProps } from "./steps";

/**
 * Step 1, "Prepare": a prompt the user copies and runs with Claude Code or Codex on their own
 * computer. That assistant looks through the folders the user names and writes one file, the
 * dossier, which the user reads and then adds in step 2. The app never reads the computer:
 * this step only shows text (GET /api/ingestion/prompt, api/app/ingestion/prompt_router.py).
 *
 * PROMPT_READY is what the page shell reads: the step is listed and can be opened.
 */
export const PROMPT_READY = true;

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

export function StepPrompt({ goTo }: StepProps) {
  const query = useQuery({
    queryKey: promptKey,
    queryFn: ({ signal }) => getPrompt(signal),
    // The text does not change while the user reads it, and it holds their name: it is not
    // kept once the step is left.
    staleTime: Infinity,
    gcTime: 0,
  });
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

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2 text-sm text-muted-foreground">
        <p>
          If you use Claude Code or Codex on your own computer, it can write a
          dossier about your work. It looks through the folders you name and
          writes one file, which you read and then add here in step 2.
        </p>
        <p>
          This step is optional. If you would rather add a CV or your own notes,
          go straight to your documents.
        </p>
      </div>

      {/* A note, not an alert: it is always there and nothing went wrong. */}
      <Alert role="note" aria-labelledby="prompt-provider-title">
        <Info aria-hidden="true" />
        <AlertTitle id="prompt-provider-title">Where your files go</AlertTitle>
        <AlertDescription>
          <p>
            Claude Code or Codex sends what it reads to its own model provider,
            under your own account. This app never reads your computer: it only
            gets the file you add in the next step.
          </p>
        </AlertDescription>
      </Alert>

      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the prompt
        </p>
      ) : query.isError ? (
        <div className="flex flex-col items-start gap-3">
          <Alert variant="destructive">
            <AlertTitle>The prompt could not be loaded</AlertTitle>
            <AlertDescription>
              {loadFailureSentence(query.error)}
            </AlertDescription>
          </Alert>
          <Button
            variant="outline"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      ) : (
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
                    Open a terminal on your computer and start {name} by typing{" "}
                    <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                      {command}
                    </code>
                    .
                  </li>
                  <li>
                    Paste the prompt, replace the two folder lines near the top
                    with the folders that hold your work, then send it.
                  </li>
                  <li>
                    Read the file it writes,{" "}
                    <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs break-words">
                      {query.data.output_folder}/dossier.md
                    </code>
                    , then add it in step 2.
                  </li>
                </ol>

                <div className="flex flex-col gap-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <label
                      htmlFor={`prompt-${id}`}
                      className="text-sm font-medium"
                    >
                      Prompt for {name}
                    </label>
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
                  <textarea
                    ref={text}
                    id={`prompt-${id}`}
                    readOnly
                    rows={14}
                    spellCheck={false}
                    value={prompt}
                    aria-describedby={`prompt-version-${id}`}
                    className="w-full resize-y rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
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
                      ? "This browser blocked copying. The prompt is selected: press Ctrl+C, or Cmd+C on a Mac."
                      : outcome === "copied"
                        ? "The prompt is on your clipboard."
                        : ""}
                  </p>
                  <p
                    id={`prompt-version-${id}`}
                    className="text-xs text-muted-foreground"
                  >
                    Prompt version {query.data.version}. Read it before you run
                    it: it lists what the assistant may read and what it must
                    leave alone.
                  </p>
                </div>
              </TabsContent>
            );
          })}
        </Tabs>
      )}

      <Button
        type="button"
        variant="outline"
        className="self-start"
        onClick={() => {
          goTo("documents");
        }}
      >
        Go on to your documents
        <ArrowRight aria-hidden="true" />
      </Button>
    </div>
  );
}
