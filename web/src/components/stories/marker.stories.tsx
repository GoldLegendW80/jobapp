import type { Meta, StoryObj } from "@storybook/react-vite";
import { HistoryIcon, Trash2Icon } from "lucide-react";

import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";

const meta = {
  title: "Components/Chat/Marker",
  component: Marker,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A note between the turns of a conversation, not a message: older messages hidden, a day that starts, a conversation deleted. separator puts the words between two rules; border closes a block above it.",
      },
    },
  },
  args: { variant: "default" },
  argTypes: {
    variant: {
      control: "inline-radio",
      options: ["default", "separator", "border"],
    },
  },
} satisfies Meta<typeof Marker>;

export default meta;
type Story = StoryObj<typeof meta>;

/** At the top of a long thread: the server sent the newest messages only. */
export const Typical: Story = {
  render: (args) => (
    <div className="max-w-md">
      <Marker {...args}>
        <MarkerIcon>
          <HistoryIcon />
        </MarkerIcon>
        <MarkerContent>Older messages are not shown.</MarkerContent>
      </Marker>
    </div>
  ),
};

/** The three variants, in a thread. */
export const Variants: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-6">
      <Marker>
        <MarkerContent>Older messages are not shown.</MarkerContent>
      </Marker>
      <Marker variant="separator">
        <MarkerContent>Today</MarkerContent>
      </Marker>
      <Marker variant="border">
        <MarkerIcon>
          <Trash2Icon />
        </MarkerIcon>
        <MarkerContent>
          The conversation before this point was deleted.
        </MarkerContent>
      </Marker>
    </div>
  ),
};

/** A link inside the note, underlined as links in sentences are. */
export const WithLink: Story = {
  render: () => (
    <div className="max-w-md">
      <Marker>
        <MarkerContent>
          The agent wrote version 4. <a href="#versions">See the versions</a>
        </MarkerContent>
      </Marker>
    </div>
  ),
};

/** A long note wraps; a separator keeps its words centred between the rules. */
export const LongText: Story = {
  render: () => (
    <div className="max-w-xs">
      <Marker variant="separator">
        <MarkerContent>
          The assistant changed your CV while this conversation was closed
        </MarkerContent>
      </Marker>
    </div>
  ),
};
