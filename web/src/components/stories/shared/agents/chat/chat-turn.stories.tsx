import type { Meta, StoryObj } from "@storybook/react-vite";

import { ChatTurn } from "@/components/shared/agents/chat/chat-turn";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

const meta = {
  title: "Shared/Agents/Chat turn",
  component: ChatTurn,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One turn of every chat (Message and Bubble): the person's in black on the right, the agent's or the assistant's in grey on the left, a message waiting its turn dashed, a line under it when there is something to say (a version, Stop, Pending). The answer being written is the same turn. In the chat about a posting and the chat of a CV or a letter.",
      },
    },
  },
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
  args: { from: "user", children: "Does this role fit my profile?" },
} satisfies Meta<typeof ChatTurn>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The person's message, on the right. */
export const FromYou: Story = { name: "From you" };

/** The agent's answer, on the left. */
export const FromTheAgent: Story = {
  name: "From the agent",
  args: {
    from: "agent",
    children:
      "Mostly, yes. The posting asks for four years of Python and SQL, which you have.",
  },
};

/** The assistant of a document's chat, with the version its change made. */
export const FromTheAssistant: Story = {
  name: "From the assistant, with its version",
  args: {
    from: "agent",
    label: "Assistant:",
    children: "Changed the summary.\nThis is version 2.",
    footer: "Version 2",
  },
};

/** A document's change being written: Stop ends it, and the document stays as it was. */
export const ChangeBeingWritten: Story = {
  name: "A change being written, with Stop",
  args: {
    from: "agent",
    label: "The assistant is answering:",
    children: (
      <span className="flex items-center gap-2 text-muted-foreground">
        <Spinner aria-hidden="true" role={undefined} />
        Changing your document
      </span>
    ),
    footer: (
      <Button type="button" variant="outline" size="xs">
        Stop
      </Button>
    ),
  },
};

/** A message of the person that waits for the answer before it is sent. */
export const WaitingToBeSent: Story = {
  name: "Waiting to be sent",
  args: {
    from: "held",
    children: "And what about the salary?",
    footer: "Pending",
  },
};

/** The answer while it is written: the line under it breathes. */
export const BeingWritten: Story = {
  name: "Being written",
  args: {
    from: "agent",
    label: "The agent is answering:",
    children: "Mostly, yes. The posting asks for four",
    footer: <span className="shimmer">Writing</span>,
  },
};

/** A long answer keeps its line breaks and wraps a long address. */
export const LongMessage: Story = {
  name: "Long message",
  args: {
    from: "agent",
    children:
      "Three things to stress:\n\n1. The pipeline you shipped and still operate, with its numbers.\n2. The on call duty you took for it.\n3. Your curiosity about logistics, with one concrete example.\n\nThe posting links to https://careers.example.org/lumen-freight/data-engineering/team-handbook-and-on-call-rotation for the team's handbook.",
  },
};

/** On a phone a bubble takes most of the width. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: {
    from: "agent",
    children:
      "Mostly, yes. The posting asks for four years of Python and SQL, which you have. Healthcare data is new to you.",
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
