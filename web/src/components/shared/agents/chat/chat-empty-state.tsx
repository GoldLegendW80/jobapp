import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";

const EXAMPLE_PROMPTS = [
  "Does this role fit my profile?",
  "What should I stress in my application?",
  "What should I check before applying?",
];

/** A conversation with no message yet, and questions to start it. Left aligned, as the
 * conversation that takes its place. */
export function ChatEmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <Empty
      data-testid="chat-empty"
      className="items-stretch gap-4 px-0 py-6 text-left text-wrap md:px-0 md:py-6"
    >
      <EmptyHeader className="max-w-none items-start gap-1 text-left">
        <EmptyTitle className="text-sm font-medium tracking-normal">
          Ask anything about this posting.
        </EmptyTitle>
        <EmptyDescription className="text-sm">
          The agent reads the posting and your profile to answer. It changes
          nothing, and it only knows what they say.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="max-w-none items-stretch text-wrap">
        <ul aria-label="Example questions" className="flex flex-col gap-2">
          {EXAMPLE_PROMPTS.map((prompt) => (
            <li key={prompt}>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-auto w-full justify-start py-2 text-left whitespace-normal"
                onClick={() => {
                  onPick(prompt);
                }}
              >
                {prompt}
              </Button>
            </li>
          ))}
        </ul>
      </EmptyContent>
    </Empty>
  );
}
