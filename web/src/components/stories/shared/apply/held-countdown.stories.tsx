import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { useState } from "react";
import { userEvent, within } from "storybook/test";

import { HeldCountdown } from "@/components/shared/apply/held-countdown";
import { inMinutes } from "@/design-system/fixtures/applications";
import { applicationIn } from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";
import { APP_ID } from "@/test/apply";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Apply/Held countdown",
  component: HeldCountdown,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      story: { inline: false, height: "420px" },
      description: {
        component:
          "The hold of a Full auto application: the time left before the app checks it once more and submits, read on the server's clock, and Cancel. In Your application and on the review queue's held cards.",
      },
    },
  },
  args: { applicationId: APP_ID, until: inMinutes(12), offset: 0 },
  // The hold ends `parameters.minutes` after the story opens, however long ago the file loaded.
  render: function Render(args, { parameters }) {
    const [until] = useState(() => inMinutes(Number(parameters.minutes ?? 12)));
    return <HeldCountdown {...args} until={until} />;
  },
  beforeEach: () => {
    server.use(
      http.post("*/api/applications/:id/cancel", async () => {
        await delay(500);
        return HttpResponse.json(
          applicationIn("cancelled", { mode: "full_auto" }),
        );
      }),
    );
  },
} satisfies Meta<typeof HeldCountdown>;

export default meta;
type Story = StoryObj<typeof meta>;

const clickCancel = async ({
  canvasElement,
}: {
  canvasElement: HTMLElement;
}) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", {
      name: "Cancel application",
    }),
  );
};

const confirmCancel = async (context: { canvasElement: HTMLElement }) => {
  await clickCancel(context);
  await userEvent.click(
    await within(document.body).findByRole("button", {
      name: "Cancel the application",
    }),
  );
};

/** Full auto submits in twelve minutes unless the person cancels; the clock ticks every second. */
export const CountingDown: Story = {
  name: "Counting down",
};

/** A long hold, set to more than an hour in Settings, shows hours. */
export const LongHold: Story = {
  name: "A hold of more than an hour",
  parameters: { minutes: 150 },
};

/** The server's clock is ahead of the page's: the time left follows the server. */
export const ServerClockAhead: Story = {
  name: "The server's clock is ahead",
  args: { offset: 5 * 60_000 },
};

/** The hold ended: the app checks once more, then submits; Cancel still works until it starts. */
export const HoldEnded: Story = {
  name: "The hold has ended",
  parameters: { minutes: -1 },
};

/** Cancel asks first: nothing is sent, unless the app already started submitting. */
export const AskBeforeCancelling: Story = {
  name: "Cancel asks first",
  play: clickCancel,
};

/** Cancelled in time: a toast says nothing was sent. */
export const CancelledInTime: Story = {
  name: "Cancelled in time",
  play: confirmCancel,
};

/** Too late: the app already started submitting, and the server's sentence says so. */
export const CancelTooLate: Story = {
  name: "Cancel comes too late",
  beforeEach: () => {
    server.use(
      http.post("*/api/applications/:id/cancel", () =>
        HttpResponse.json(
          {
            detail:
              "Too late to cancel: the app already started submitting this application.",
            code: "too_late",
            state: "submitting",
          },
          { status: 409 },
        ),
      ),
    );
  },
  play: confirmCancel,
};

/** On a phone, Cancel takes the whole width. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
