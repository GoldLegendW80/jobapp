import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";

/** A conversation with no message yet: what the chat does, and examples that fill the prompt.
 * Left aligned, as the conversation that takes its place. */
export function ChatEmptyState({
  title,
  description,
  examples,
  examplesLabel,
  testId,
  onPick,
}: {
  title?: ReactNode;
  description: ReactNode;
  examples: readonly string[];
  /** The accessible name of the list of examples. */
  examplesLabel: string;
  testId: string;
  onPick: (text: string) => void;
}) {
  return (
    <Empty
      data-testid={testId}
      className="items-stretch gap-4 px-0 py-6 text-left text-wrap md:px-0 md:py-6"
    >
      <EmptyHeader className="max-w-none items-start gap-1 text-left">
        {title ? (
          <EmptyTitle className="text-sm font-medium tracking-normal">
            {title}
          </EmptyTitle>
        ) : null}
        <EmptyDescription className="text-sm">{description}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="max-w-none items-stretch text-wrap">
        <ul aria-label={examplesLabel} className="flex flex-col gap-2">
          {examples.map((example) => (
            <li key={example}>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-auto w-full justify-start py-2 text-left whitespace-normal"
                onClick={() => {
                  onPick(example);
                }}
              >
                {example}
              </Button>
            </li>
          ))}
        </ul>
      </EmptyContent>
    </Empty>
  );
}
