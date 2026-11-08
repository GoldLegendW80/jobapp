import { ArrowRight, Info } from "lucide-react";

import { PageBody } from "@/components/blocks/page";
import { AssistantPrompt } from "@/components/shared/ingestion/assistant-prompt";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import type { StepProps } from "../helpers/steps";

/**
 * Step 1, "Prepare": a prompt the user copies and runs with Claude Code or Codex on their own
 * computer. That assistant looks through the home folder and answers with the profile, which the
 * user reads, copies and pastes in step 2. The app never reads the computer:
 * this step only shows text (GET /api/ingestion/prompt, api/app/ingestion/prompt_router.py).
 *
 * PROMPT_READY is what the page shell reads: the step is listed and can be opened.
 */
export const PROMPT_READY = true;

export function StepPrompt({ goTo }: StepProps) {
  return (
    <PageBody
      aside={
        <>
          {/* A note, not an alert: it is always there and nothing went wrong. */}
          <Alert role="note" aria-labelledby="prompt-provider-title">
            <Info aria-hidden="true" />
            <AlertTitle id="prompt-provider-title">
              Where your files go
            </AlertTitle>
            <AlertDescription>
              <p>
                Claude Code or Codex sends what it reads to its own model
                provider, under your own account. This app never reads your
                computer: it only gets the answer you paste in the next step.
              </p>
            </AlertDescription>
          </Alert>
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
        </>
      }
    >
      <div className="flex max-w-prose flex-col gap-2 text-sm text-muted-foreground">
        <p>
          If you use Claude Code or Codex on your own computer, it can look
          through it and answer with everything about your work, which you read,
          copy and paste here in step 2.
        </p>
        <p>
          This step is optional. If you would rather add a CV or your own notes,
          go straight to your documents.
        </p>
      </div>

      <AssistantPrompt addIt="paste it in step 2, under Or paste a text" />
    </PageBody>
  );
}
