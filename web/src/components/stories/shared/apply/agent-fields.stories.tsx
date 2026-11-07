import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { fn, userEvent, within } from "storybook/test";

import { AgentFields } from "@/components/shared/apply/agent-fields";
import {
  AGENT_FILLED,
  AGENT_FILLED_LONG,
  applicationIn,
} from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const READY = applicationIn("ready", { mode: "review" });

const meta = {
  title: "Shared/Apply/Agent fields",
  component: AgentFields,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Review first with the apply agent: every field the assistant filled, step by step, from its own recorded acts, then Confirm and send, which clicks the form's own Submit and nothing else. In Your application on a posting.",
      },
    },
  },
  args: { application: READY, filled: AGENT_FILLED, onChanged: fn() },
  beforeEach: () => {
    server.use(
      http.post("*/api/applications/:id/confirm", async () => {
        await delay(600);
        return HttpResponse.json(applicationIn("approved", { mode: "review" }));
      }),
      http.post("*/api/applications/:id/cancel", async () => {
        await delay(600);
        return HttpResponse.json(
          applicationIn("cancelled", { mode: "review" }),
        );
      }),
    );
  },
} satisfies Meta<typeof AgentFields>;

export default meta;
type Story = StoryObj<typeof meta>;

const confirm = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", {
      name: "Confirm and send",
    }),
  );
};

/** Filled and waiting: the person reads every field, then confirms or cancels. Nothing is sent before. */
export const CheckThenConfirm: Story = {
  name: "Check what was filled, then confirm",
};

/** After Confirm, both buttons wait for the server. */
export const Confirming: Story = {
  name: "Confirming",
  beforeEach: () => {
    server.use(
      http.post("*/api/applications/:id/confirm", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
  play: confirm,
};

/** The form moved since the list was made: the server refuses, its sentence shows, and the page reads again. */
export const ConfirmRefused: Story = {
  name: "Confirm is refused",
  beforeEach: () => {
    server.use(
      http.post("*/api/applications/:id/confirm", () =>
        HttpResponse.json(
          {
            detail:
              "The form changed since this list was made. The assistant fills it again and shows you a new list.",
            code: "stale",
          },
          { status: 409 },
        ),
      ),
    );
  },
  play: confirm,
};

/** Once confirmed or sent, the list stays as a record, without buttons. */
export const AfterConfirm: Story = {
  name: "After Confirm, a record",
  args: { application: applicationIn("submitted", { mode: "review" }) },
};

/** A long answer keeps its line breaks and wraps. */
export const LongAnswers: Story = {
  name: "Long answers",
  args: { filled: AGENT_FILLED_LONG },
};

/** On a phone, the buttons stack and take the whole width. */
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
