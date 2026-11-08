import { ModelAccess } from "@/components/shared/agents/model-access";

/**
 * Step 1, Model access: the Claude subscription token or the Anthropic API key the agents run on,
 * as in Account. Reading documents, writing CVs and searching wait for it; skippable all the same.
 */
export function StepAccess() {
  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-prose text-sm text-muted-foreground">
        The app's agents run on your own Claude access: a token from your Claude
        subscription, or an Anthropic API key. Reading your documents, writing
        your CVs and searching for jobs wait until one is saved. It is stored
        encrypted, and only its last four characters are ever shown.
      </p>
      <ModelAccess />
    </div>
  );
}
