import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { fn } from "storybook/test";

import { ChatPrompt } from "@/components/shared/agents/chat/chat-prompt";
import { Kbd } from "@/components/ui/kbd";

const meta = {
  title: "Shared/Agents/Chat prompt",
  component: ChatPrompt,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The box under every chat: the message, Send inside it, a hint that says what Enter does, and the count of characters when the chat shows it. Under the chat about a posting and the chat of a CV or a letter.",
      },
    },
  },
  args: {
    value: "",
    max: 2000,
    placeholder: "Ask about this job",
    canSend: true,
    sending: false,
    onChange: fn(),
    onSubmit: fn(),
  },
  render: function Render(args) {
    const [value, setValue] = useState(args.value);
    return (
      <ChatPrompt
        {...args}
        value={value}
        canSend={args.canSend && value.trim() !== ""}
        onChange={(text) => {
          setValue(text);
          args.onChange(text);
        }}
      />
    );
  },
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ChatPrompt>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Empty: Send waits for a message. */
export const ReadyToType: Story = { name: "Ready to type" };

/** A message typed: Enter or Send sends it. */
export const WithAMessage: Story = {
  name: "With a message typed",
  args: { value: "Is the team remote on Fridays?" },
};

/** The message is on its way. */
export const Sending: Story = {
  name: "Sending",
  args: {
    value: "Is the team remote on Fridays?",
    canSend: false,
    sending: true,
  },
};

/** The answer is being written: Enter holds the next message until it is complete. */
export const WhileTheAnswerIsWritten: Story = {
  name: "While the answer is written",
  args: {
    value: "And the salary?",
    canSend: false,
    hint: (
      <>
        <Kbd>Enter</Kbd> sends your message once the answer is complete.
      </>
    ),
  },
};

/** Enter was pressed while the answer was written: the message waits, and an edit cancels it. */
export const MessageHeld: Story = {
  name: "Message waiting to be sent",
  args: {
    value: "And the salary?",
    canSend: false,
    hint: "Your message will be sent when the answer is complete. Edit or clear it to cancel.",
  },
};

/** The chat of a document counts the characters against its limit. */
export const WithTheCount: Story = {
  name: "With the count of characters",
  args: {
    value: "Make the summary shorter",
    placeholder: "Ask for a change",
    showCount: true,
  },
};

/** One message at a time: the box waits for the answer. */
export const OneAtATime: Story = {
  name: "One message at a time",
  args: {
    value: "",
    placeholder: "Ask for a change",
    showCount: true,
    canSend: false,
    hint: "One message at a time: you can send the next one once this one is answered.",
  },
};

/** No consent or no model credential: the box takes no text. */
export const Off: Story = {
  name: "Taking no text",
  args: { disabled: true, canSend: false },
};

/** On a phone, the box takes the width of the screen. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { value: "Is the team remote on Fridays?", showCount: true },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
