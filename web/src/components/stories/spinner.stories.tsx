import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";

const meta = {
  title: "Components/Feedback/Spinner",
  component: Spinner,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Says a wait has started when its end is unknown: a button sending, a row saving, a run starting. Pair it with words for what is happening; use Progress when the end is known, Skeleton when the shape of the content is.",
      },
    },
  },
} satisfies Meta<typeof Spinner>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Beside the words of what runs. The words carry the meaning, so the spinner is hidden from screen readers. */
export const Typical: Story = {
  render: () => (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Spinner aria-hidden="true" role={undefined} />
      Reading your documents
    </div>
  ),
};

/** Alone, it is a status named Loading. Use it alone only where the place says what loads. */
export const Alone: Story = {
  render: () => <Spinner />,
};

/** In a button: the button is disabled and its label says the action in progress. */
export const InButton: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button disabled>
        <Spinner aria-hidden="true" role={undefined} />
        Applying
      </Button>
      <Button variant="outline" disabled>
        <Spinner aria-hidden="true" role={undefined} />
        Saving
      </Button>
    </div>
  ),
};

/** It takes the colour of the text around it and its size from size-*. */
export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      <Spinner className="size-3" />
      <Spinner />
      <Spinner className="size-6" />
      <Spinner className="size-8 text-primary" />
    </div>
  ),
};

/** A region that waits, such as the live browser before its first frame. */
export const InRegion: Story = {
  render: () => (
    <Card className="h-48 max-w-md items-center justify-center gap-2">
      <Spinner aria-hidden="true" role={undefined} className="size-6" />
      <span className="text-sm text-muted-foreground">Opening the browser</span>
    </Card>
  ),
};

/** For short waits: delayed-appear shows it only after 400 ms, so a fast answer never flashes it. */
export const Delayed: Story = {
  render: () => (
    <div className="delayed-appear flex items-center gap-2 text-sm text-muted-foreground">
      <Spinner aria-hidden="true" role={undefined} />
      Checking the server
    </div>
  ),
};
