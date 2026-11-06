import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  ExternalLinkIcon,
  HeartIcon,
  MoreHorizontalIcon,
  SendIcon,
  Trash2Icon,
} from "lucide-react";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

const VARIANTS = [
  "default",
  "secondary",
  "outline",
  "ghost",
  "destructive",
  "link",
] as const;

const meta = {
  title: "Components/Actions/Button",
  component: Button,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Starts an action. One primary (default) button per group: the thing the person came to do. The label names exactly what happens, and the toast that follows uses the same verb.",
      },
    },
  },
  args: { children: "Apply" },
  argTypes: {
    variant: { control: "inline-radio", options: VARIANTS },
    size: {
      control: "inline-radio",
      options: ["xs", "sm", "default", "lg"],
    },
  },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The primary action of a posting. */
export const Typical: Story = {};

/** In priority order. Default for the one primary action, secondary and outline beside it, ghost in toolbars and rows, destructive only to confirm a loss, link inside running text. */
export const Variants: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button>Apply</Button>
      <Button variant="secondary">Write a CV</Button>
      <Button variant="outline">Pass</Button>
      <Button variant="ghost">Open posting</Button>
      <Button variant="destructive">Delete posting</Button>
      <Button variant="link">See the run report</Button>
    </div>
  ),
};

/** xs inside dense rows, sm in toolbars and table rows, default in forms and dialogs, lg for the single action of an empty page. */
export const Sizes: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button size="xs">Apply</Button>
      <Button size="sm">Apply</Button>
      <Button>Apply</Button>
      <Button size="lg">Apply</Button>
    </div>
  ),
};

/** An icon leads when it says what the action does, trails when it says where it goes. An icon alone needs an accessible name. */
export const WithIcon: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button>
        <SendIcon />
        Confirm and send
      </Button>
      <Button variant="outline">
        Open on the website
        <ExternalLinkIcon />
      </Button>
      <Button variant="outline" size="icon" aria-label="Like">
        <HeartIcon />
      </Button>
      <Button variant="ghost" size="icon-sm" aria-label="More actions">
        <MoreHorizontalIcon />
      </Button>
      <Button variant="destructive" size="sm">
        <Trash2Icon />
        Delete
      </Button>
    </div>
  ),
};

/** Every variant in every state, pinned. Loading is a Spinner plus disabled, never a new prop. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={VARIANTS}
      states={["default", "hover", "focus", "active", "disabled", "loading"]}
      render={(variant, state) =>
        state === "loading" ? (
          <Button variant={variant} disabled>
            <Spinner />
            Applying
          </Button>
        ) : (
          <Button variant={variant} {...previewState(state)}>
            Apply
          </Button>
        )
      }
    />
  ),
};

/** A long label never wraps inside a button: shorten the words, or let the row wrap the buttons. */
export const LongLabel: Story = {
  render: () => (
    <div className="flex max-w-sm flex-wrap gap-3">
      <Button>Approve all ready applications</Button>
      <Button variant="outline">Cancel</Button>
    </div>
  ),
};
