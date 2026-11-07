import type { Meta, StoryObj } from "@storybook/react-vite";
import { CheckIcon, XIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Bubble,
  BubbleContent,
  BubbleGroup,
  BubbleReactions,
} from "@/components/ui/bubble";
import { cn } from "@/lib/utils";

const VARIANTS = [
  ["default", "The person's own message"],
  ["muted", "The agent's answer"],
  ["secondary", "An answer on a muted surface"],
  ["tinted", "A message to notice: a change the agent made"],
  ["outline", "A message that waits to be sent"],
  ["ghost", "A long answer read as text, without a surface"],
  ["destructive", "A message the server refused"],
] as const;

const meta = {
  title: "Components/Chat/Bubble",
  component: Bubble,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The surface of a message, inside a Message. Near-black on the person's side (end), muted on the agent's (start); the corner nearest the sender is tight. Text is plain: add whitespace-pre-wrap where line breaks of the message matter.",
      },
    },
  },
  args: { variant: "muted", align: "start" },
  argTypes: {
    variant: {
      control: "inline-radio",
      options: VARIANTS.map(([variant]) => variant),
    },
    align: { control: "inline-radio", options: ["start", "end"] },
  },
} satisfies Meta<typeof Bubble>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The agent's answer. */
export const Typical: Story = {
  render: (args) => (
    <div className="flex max-w-md flex-col">
      <Bubble {...args}>
        <BubbleContent>
          Mostly: you have 7 of the 9 skills it asks for.
        </BubbleContent>
      </Bubble>
    </div>
  ),
};

/** Every variant, with what it is for. */
export const Variants: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4">
      {VARIANTS.map(([variant, use]) => (
        <div key={variant} className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">
            {variant}: {use}
          </span>
          <Bubble
            variant={variant}
            align={
              variant === "default" || variant === "outline" ? "end" : "start"
            }
          >
            <BubbleContent
              className={cn(variant === "outline" && "border-dashed")}
            >
              Make the summary shorter
            </BubbleContent>
          </Bubble>
        </div>
      ))}
    </div>
  ),
};

/** Stacked bubbles of one turn sit in a BubbleGroup. */
export const Grouped: Story = {
  render: () => (
    <BubbleGroup className="max-w-md">
      <Bubble variant="muted">
        <BubbleContent>Done: the summary is now three lines.</BubbleContent>
      </Bubble>
      <Bubble variant="tinted">
        <BubbleContent>The fact check removed one claim.</BubbleContent>
      </Bubble>
    </BubbleGroup>
  ),
};

/** A reaction or a count pinned to the bubble, out of the text's way. */
export const WithReaction: Story = {
  render: () => (
    <div className="flex max-w-md flex-col pb-4">
      <Bubble variant="muted">
        <BubbleContent>Your letter is ready to read.</BubbleContent>
        <BubbleReactions>
          <Badge variant="secondary">Version 3</Badge>
        </BubbleReactions>
      </Bubble>
    </div>
  ),
};

/** A long word or a link breaks inside the bubble instead of pushing it wider. */
export const LongText: Story = {
  render: () => (
    <div className="flex max-w-xs flex-col">
      <Bubble align="end">
        <BubbleContent>
          Read https://jobs.example.org/lumen-freight/senior-data-engineer-lyon
          and tell me what they want most
        </BubbleContent>
      </Bubble>
    </div>
  ),
};

function Pair({ good, bad }: { good: ReactNode; bad: ReactNode }) {
  return (
    <div className="grid max-w-3xl gap-4 md:grid-cols-2">
      {(
        [
          ["Do", good, CheckIcon, "text-success"],
          ["Don't", bad, XIcon, "text-destructive"],
        ] as const
      ).map(([label, example, Icon, tone]) => (
        <figure
          key={label}
          className="flex flex-col gap-3 rounded-lg border bg-card p-4"
        >
          <figcaption
            className={cn(
              "flex items-center gap-1.5 text-sm font-medium",
              tone,
            )}
          >
            <Icon aria-hidden="true" className="size-4" />
            {label}
          </figcaption>
          {example}
        </figure>
      ))}
    </div>
  );
}

/** A Bubble, not a styled div: one shape, one set of colours, focus and hover handled. */
export const DoAndDont: Story = {
  name: "Do and don't",
  render: () => (
    <Pair
      good={
        <Bubble align="end">
          <BubbleContent>Make the summary shorter</BubbleContent>
        </Bubble>
      }
      bad={
        <div className="ml-auto w-fit rounded-lg bg-blue-600 px-4 py-1 text-white">
          Make the summary shorter
        </div>
      }
    />
  ),
};
