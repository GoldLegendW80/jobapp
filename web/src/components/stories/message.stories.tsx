import type { Meta, StoryObj } from "@storybook/react-vite";
import { BotIcon, CircleAlertIcon } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import {
  Message,
  MessageAvatar,
  MessageContent,
  MessageFooter,
  MessageGroup,
  MessageHeader,
} from "@/components/ui/message";

const meta = {
  title: "Components/Chat/Message",
  component: Message,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One turn of a conversation: who wrote it (an avatar, a header), its Bubble, and a footer for the time, the version a change made, or why no answer came. align end is the person, start is the agent. Consecutive turns of one sender go in a MessageGroup.",
      },
    },
  },
} satisfies Meta<typeof Message>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The person asks, the agent answers: one row each, on its own side. */
export const Typical: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4">
      <Message align="end">
        <MessageContent>
          <Bubble align="end">
            <BubbleContent>Make the summary shorter</BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
      <Message align="start">
        <MessageContent>
          <Bubble variant="muted">
            <BubbleContent>
              Done: the summary is now three lines. The fact check changed
              nothing.
            </BubbleContent>
          </Bubble>
          <MessageFooter>Version 4</MessageFooter>
        </MessageContent>
      </Message>
    </div>
  ),
};

/** With an avatar and a header, where several senders share a thread. */
export const WithAvatarAndHeader: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4">
      <Message align="start">
        <MessageAvatar>
          <Avatar>
            <AvatarFallback>
              <BotIcon aria-hidden="true" className="size-4" />
            </AvatarFallback>
          </Avatar>
        </MessageAvatar>
        <MessageContent>
          <MessageHeader>Assistant</MessageHeader>
          <Bubble variant="muted">
            <BubbleContent>
              Your CV for Lumen Freight is ready. I put the depot pipelines
              first.
            </BubbleContent>
          </Bubble>
          <MessageFooter>Today at 09:42</MessageFooter>
        </MessageContent>
      </Message>
      <Message align="end">
        <MessageAvatar>
          <Avatar>
            <AvatarFallback>CM</AvatarFallback>
          </Avatar>
        </MessageAvatar>
        <MessageContent>
          <MessageHeader>You</MessageHeader>
          <Bubble align="end">
            <BubbleContent>Use the sidebar template</BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    </div>
  ),
};

/** Several turns in a row from one sender: one group, tighter. */
export const Grouped: Story = {
  render: () => (
    <MessageGroup className="max-w-md">
      {[
        "Put my most relevant experience first",
        "And drop the hobbies section",
      ].map((text) => (
        <Message key={text} align="end">
          <MessageContent>
            <Bubble align="end">
              <BubbleContent>{text}</BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      ))}
    </MessageGroup>
  ),
};

/** A message that got no answer says why in its footer, in the destructive colour. */
export const NoAnswer: Story = {
  render: () => (
    <Message align="end" className="max-w-md">
      <MessageContent>
        <Bubble align="end">
          <BubbleContent>What should I check before applying?</BubbleContent>
        </Bubble>
        <MessageFooter className="gap-1 text-destructive">
          <CircleAlertIcon aria-hidden="true" className="size-3.5" />
          No answer: the agent failed.
        </MessageFooter>
      </MessageContent>
    </Message>
  ),
};

/** A message that waits for the answer before it is sent: outlined, with a way to cancel it. */
export const Pending: Story = {
  render: () => (
    <Message align="end" className="max-w-md">
      <MessageContent>
        <Bubble align="end" variant="outline">
          <BubbleContent className="border-dashed">
            And the cover letter, in French
          </BubbleContent>
        </Bubble>
        <MessageFooter className="gap-2">
          Pending
          <Button variant="ghost" size="xs">
            Cancel
          </Button>
        </MessageFooter>
      </MessageContent>
    </Message>
  ),
};

/** A long answer wraps inside the bubble, which stops at 85% of the thread's width. */
export const LongText: Story = {
  render: () => (
    <Message align="start" className="max-w-md">
      <MessageContent>
        <Bubble variant="muted">
          <BubbleContent className="whitespace-pre-wrap">
            {
              "The posting asks for 9 skills and you have 7 of them: Python, SQL, Airflow, dbt, PostgreSQL, Docker and Terraform. Kafka and Spark are missing from your profile.\n\nThe role is senior and so are you, with eight years in data engineering. The pay is within your range, and Lyon is one of your places, so neither takes points off. A link that never breaks: https://jobs.example.org/lumen-freight/senior-data-engineer-lyon-2026"
            }
          </BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
  ),
};
