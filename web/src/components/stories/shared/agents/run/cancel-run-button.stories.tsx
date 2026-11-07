import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";

import { CancelRunButton } from "@/components/shared/agents/run/cancel-run-button";
import { makeRun, RUN_ID } from "@/design-system/fixtures/runs";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Agents/Cancel run button",
  component: CancelRunButton,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Cancel of a queued or running agent run: in the run panel's header and on the page of a run. It says Stopping once asked, until the run ends; a toast says what happened.",
      },
    },
  },
  args: { runId: RUN_ID, stopping: false },
} satisfies Meta<typeof CancelRunButton>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A click asks the run to stop; the toast says it is stopping. */
export const CancelARunningRun: Story = {
  name: "Cancel a running run",
  beforeEach: () => {
    server.use(
      http.post("*/api/agents/runs/:id/cancel", async () => {
        await delay(600);
        return HttpResponse.json(
          makeRun({ status: "running", cancel_requested: true }),
        );
      }),
    );
  },
};

/** Asked to stop, the run has not ended yet: the button waits. */
export const Stopping: Story = {
  name: "Stopping",
  args: { stopping: true },
};

/** The small size the run panel puts in its header. */
export const Small: Story = {
  name: "Small, in a panel header",
  args: { size: "sm" },
};

/** The api refuses: the toast says why, and the button can be clicked again. */
export const CancelRefused: Story = {
  name: "Cancel refused",
  beforeEach: () => {
    server.use(
      http.post("*/api/agents/runs/:id/cancel", () =>
        HttpResponse.json(
          { detail: "This run has already ended." },
          { status: 409 },
        ),
      ),
    );
  },
};
