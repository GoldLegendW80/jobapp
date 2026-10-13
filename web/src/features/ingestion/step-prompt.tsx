import type { StepProps } from "./steps";

/**
 * Step 1, "Prepare": the prompt to copy into Claude Code or Codex, which writes a dossier about
 * the user from their own computer. A placeholder until task G3 builds it in this file.
 *
 * PROMPT_READY is what the page shell reads: while it is false the step shows as "Soon" in the
 * steps and cannot be opened; G3 sets it to true and replaces the panel below.
 */
export const PROMPT_READY = false;

export function StepPrompt({ goTo }: StepProps) {
  return (
    <div className="flex flex-col items-start gap-3 text-sm text-muted-foreground">
      <p>
        Soon, this step will give you a ready made request for a coding
        assistant on your own computer. It will look through the folders you
        name and write a few files about you, which you then add here.
      </p>
      <p>You can already add the documents you have.</p>
      <button
        type="button"
        className="font-medium text-foreground underline underline-offset-4"
        onClick={() => {
          goTo("documents");
        }}
      >
        Go to your documents
      </button>
    </div>
  );
}
