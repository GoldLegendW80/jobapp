import type { Meta, StoryObj } from "@storybook/react-vite";
import { CircleAlertIcon, SendHorizontalIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupText,
  InputGroupTextarea,
} from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import { Marker, MarkerContent } from "@/components/ui/marker";
import {
  Message,
  MessageContent,
  MessageFooter,
} from "@/components/ui/message";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface Turn {
  id: string;
  role: "user" | "assistant";
  text: string;
}

const THREAD: Turn[] = [
  {
    id: "m1",
    role: "user",
    text: "Does this role fit my profile?",
  },
  {
    id: "m2",
    role: "assistant",
    text: "Mostly. The posting asks for 9 skills and your profile has 7 of them: Python, SQL, Airflow, dbt, PostgreSQL, Docker and Terraform. Kafka and Spark are missing.\n\nThe pay, 55,000 to 65,000 EUR, is inside your range, and Lyon is one of your places.",
  },
  {
    id: "m3",
    role: "user",
    text: "What should I stress in my application?",
  },
  {
    id: "m4",
    role: "assistant",
    text: "Your two years running the depot pipelines at Atelier Brume: they ask for pipelines kept running for more than a year. Then the dbt models you wrote for the analysts.",
  },
];

/** A long conversation, to scroll: the same questions, asked about several postings. */
const LONG: Turn[] = Array.from({ length: 6 }, (_, round) =>
  THREAD.map((turn) => ({ ...turn, id: `${turn.id}-${String(round)}` })),
).flat();

const ANSWER =
  "Start with the pipelines you kept running at Atelier Brume for two years, since they ask for that in so many words. Then the dbt models you wrote for the analysts, and the queries you made faster. Leave Kafka out: say instead that you learn new tools on the job, with the Terraform you picked up last year as the example.";

function Turns({ turns }: { turns: Turn[] }) {
  return turns.map((turn) => (
    <MessageScrollerItem
      key={turn.id}
      messageId={turn.id}
      scrollAnchor={turn.role === "user"}
    >
      <Message align={turn.role === "user" ? "end" : "start"}>
        <span className="sr-only">
          {turn.role === "user" ? "You:" : "Agent:"}
        </span>
        <MessageContent>
          <Bubble
            align={turn.role === "user" ? "end" : "start"}
            variant={turn.role === "user" ? "default" : "muted"}
          >
            <BubbleContent className="whitespace-pre-wrap">
              {turn.text}
            </BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    </MessageScrollerItem>
  ));
}

/** The prompt under a conversation: an InputGroup, never a Textarea beside a Button. */
function Composer({ disabled = false }: { disabled?: boolean }) {
  return (
    <form
      aria-label="Send a message"
      className="border-t p-4"
      onSubmit={(event) => {
        event.preventDefault();
      }}
    >
      <InputGroup data-disabled={disabled}>
        <InputGroupTextarea
          aria-label="Message"
          placeholder="Ask about this job"
          rows={2}
          disabled={disabled}
        />
        <InputGroupAddon align="block-end">
          <InputGroupText>
            <span>
              <Kbd>Enter</Kbd> sends, <Kbd>Shift</Kbd> and <Kbd>Enter</Kbd> adds
              a line.
            </span>
          </InputGroupText>
          <InputGroupButton
            type="submit"
            variant="default"
            size="icon-sm"
            className="ml-auto"
            aria-label="Send"
            disabled={disabled}
          >
            <SendHorizontalIcon aria-hidden="true" />
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
    </form>
  );
}

/** The chat panel's frame: a scrolling thread over the prompt, as tall as the panel. */
function Panel({
  children,
  height = "h-[32rem]",
}: {
  children: ReactNode;
  height?: string;
}) {
  return (
    <Card className={cn("max-w-md gap-0 overflow-hidden py-0", height)}>
      {children}
    </Card>
  );
}

function Thread({
  turns,
  before,
  children,
}: {
  turns: Turn[];
  /** Rows above the turns: a note that older messages are hidden. */
  before?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <MessageScrollerProvider autoScroll>
      <MessageScroller>
        <MessageScrollerViewport>
          <MessageScrollerContent className="p-4" aria-live="off">
            {before}
            <Turns turns={turns} />
            {children}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  );
}

const meta = {
  title: "Components/Chat/Message scroller",
  component: MessageScroller,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The scrolling thread of a conversation: the chat about a posting, the chat that changes a document. It opens on the newest message, follows an answer as it is written while the person is at the bottom, lets go when they scroll up, and offers a button back to the newest message. Each row is a MessageScrollerItem; the person's messages anchor their turn. The content is role log: a page that announces each new answer itself sets aria-live off on it.",
      },
    },
  },
} satisfies Meta<typeof MessageScroller>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The chat about a posting: two questions, two answers, the prompt under them. */
export const Typical: Story = {
  render: () => (
    <Panel>
      <Thread turns={THREAD} />
      <Composer />
    </Panel>
  ),
};

/** A long thread opens on its newest message; scroll up and the button brings it back. */
export const LongConversation: Story = {
  render: () => (
    <Panel>
      <Thread
        turns={LONG}
        before={
          <MessageScrollerItem messageId="older">
            <Marker variant="separator">
              <MarkerContent>Older messages are not shown</MarkerContent>
            </Marker>
          </MessageScrollerItem>
        }
      />
      <Composer />
    </Panel>
  ),
};

function StreamingAnswer() {
  const words = ANSWER.split(" ");
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (shown >= words.length) return;
    const timer = window.setTimeout(() => {
      setShown((count) => count + 1);
    }, 90);
    return () => {
      window.clearTimeout(timer);
    };
  }, [shown, words.length]);
  const writing = shown < words.length;
  return (
    <Panel>
      <Thread
        turns={[
          ...THREAD,
          { id: "m5", role: "user", text: "How do I answer the Kafka gap?" },
        ]}
      >
        <MessageScrollerItem messageId="live">
          <Message align="start" aria-busy={writing}>
            <MessageContent>
              <Bubble variant="muted">
                <BubbleContent className="whitespace-pre-wrap">
                  {words.slice(0, Math.max(shown, 1)).join(" ")}
                </BubbleContent>
              </Bubble>
              <MessageFooter>{writing ? "Writing" : "Just now"}</MessageFooter>
            </MessageContent>
          </Message>
        </MessageScrollerItem>
      </Thread>
      <Composer />
    </Panel>
  );
}

/** An answer being written: the thread follows it while the person stays at the bottom. */
export const Streaming: Story = {
  render: () => <StreamingAnswer />,
};

/** No message yet: questions to start with, in place of the thread. */
export const Empty: Story = {
  render: () => (
    <Panel>
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
        <p className="text-sm font-medium">Ask anything about this posting.</p>
        <p className="text-sm text-muted-foreground">
          The agent reads the posting and your profile to answer. It changes
          nothing.
        </p>
        {[
          "Does this role fit my profile?",
          "What should I stress in my application?",
        ].map((prompt) => (
          <Button
            key={prompt}
            variant="outline"
            size="sm"
            className="h-auto justify-start py-2 text-left whitespace-normal"
          >
            {prompt}
          </Button>
        ))}
      </div>
      <Composer />
    </Panel>
  ),
};

/** Loading: bubbles of the thread's shape hold the place. */
export const Loading: Story = {
  render: () => (
    <Panel>
      <div
        role="status"
        aria-label="Loading the conversation"
        className="flex min-h-0 flex-1 flex-col gap-4 p-4"
      >
        <Skeleton className="ml-auto h-9 w-48 rounded-2xl" />
        <Skeleton className="h-20 w-64 rounded-2xl" />
        <Skeleton className="ml-auto h-9 w-40 rounded-2xl" />
      </div>
      <Composer disabled />
    </Panel>
  ),
};

/** The thread could not be loaded: say so where it would be, and keep the prompt off. */
export const Error: Story = {
  render: () => (
    <Panel>
      <div className="flex min-h-0 flex-1 flex-col p-4">
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>The conversation could not be loaded</AlertTitle>
          <AlertDescription>
            The server did not answer. Try again in a moment.
          </AlertDescription>
        </Alert>
      </div>
      <Composer disabled />
    </Panel>
  ),
};

/** On a phone the panel takes the screen: the same thread, narrower bubbles. */
export const OnAPhone: Story = {
  render: () => (
    <div className="max-w-sm">
      <Panel height="h-[36rem]">
        <Thread turns={THREAD} />
        <Composer />
      </Panel>
    </div>
  ),
};
