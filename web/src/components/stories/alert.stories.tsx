import type { Meta, StoryObj } from "@storybook/react-vite";
import { CircleAlertIcon, InfoIcon, KeyRoundIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

const meta = {
  title: "Components/Feedback/Alert",
  component: Alert,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A callout that stays on the page until its cause is gone: a missing model key, a run that failed, what a setting will do. Use a toast for news about an action that just happened, AlertDialog to stop and confirm.",
      },
    },
  },
  args: { variant: "default" },
  argTypes: {
    variant: { control: "inline-radio", options: ["default", "destructive"] },
  },
} satisfies Meta<typeof Alert>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Something the person should know before going on, with the one action that settles it. */
export const Typical: Story = {
  render: (args) => (
    <Alert {...args} className="max-w-xl">
      <KeyRoundIcon />
      <AlertTitle>CVs and letters need a model key</AlertTitle>
      <AlertDescription>
        <p>
          The app writes them with your own Anthropic API key. Until one is
          saved, postings are found and scored but nothing is written.
        </p>
        <Button size="sm" className="mt-2">
          Add a model key
        </Button>
      </AlertDescription>
    </Alert>
  ),
};

/** A title alone, when one short sentence says it all. */
export const TitleOnly: Story = {
  render: (args) => (
    <Alert {...args} className="max-w-xl">
      <AlertTitle>Full auto is on: liked postings are sent for you.</AlertTitle>
    </Alert>
  ),
};

/** Add a description for the why and the what next. */
export const WithDescription: Story = {
  render: (args) => (
    <Alert {...args} className="max-w-xl">
      <AlertTitle>Full auto is on</AlertTitle>
      <AlertDescription>
        Each posting you like is applied to without a review. Switch to Review
        first to see each filled form before it is sent.
      </AlertDescription>
    </Alert>
  ),
};

/** Add an icon that says the kind of message at a glance. */
export const WithIcon: Story = {
  render: (args) => (
    <Alert {...args} className="max-w-xl">
      <InfoIcon />
      <AlertTitle>Full auto is on</AlertTitle>
      <AlertDescription>
        Each posting you like is applied to without a review. Switch to Review
        first to see each filled form before it is sent.
      </AlertDescription>
    </Alert>
  ),
};

/** Destructive for a failure: what happened, then what to do about it. */
export const Destructive: Story = {
  args: { variant: "destructive" },
  render: (args) => (
    <Alert {...args} className="max-w-xl">
      <CircleAlertIcon />
      <AlertTitle>The application form could not be sent</AlertTitle>
      <AlertDescription>
        <p>
          The site asked for a code sent to your phone. Open the live browser,
          type the code, then let the assistant continue.
        </p>
        <Button size="sm" className="mt-2">
          Open the live browser
        </Button>
      </AlertDescription>
    </Alert>
  ),
};

/** A long message keeps its icon at the top and wraps; the title stays on one line. */
export const LongText: Story = {
  render: (args) => (
    <Alert {...args} className="max-w-xl">
      <InfoIcon />
      <AlertTitle>
        Your profile on Welcome to the Jungle is complete, and three sections
        changed since your last review
      </AlertTitle>
      <AlertDescription>
        The assistant filled Experience, Education and Languages from your
        profile. Existing values on the site stay until you select a new one.
        Look at each section, change what reads wrong, then save: nothing is
        saved on the site before you do.
      </AlertDescription>
    </Alert>
  ),
};

/** Both kinds side by side, to compare them in each theme. */
export const Variants: Story = {
  render: () => (
    <div className="flex max-w-xl flex-col gap-3">
      <Alert>
        <InfoIcon />
        <AlertTitle>Reading paused</AlertTitle>
        <AlertDescription>
          The reading paused at the limit you agreed to.
        </AlertDescription>
      </Alert>
      <Alert variant="destructive">
        <CircleAlertIcon />
        <AlertTitle>Server unreachable</AlertTitle>
        <AlertDescription>
          Your changes are kept on this page. Check the server again in a
          moment.
        </AlertDescription>
      </Alert>
    </div>
  ),
};
