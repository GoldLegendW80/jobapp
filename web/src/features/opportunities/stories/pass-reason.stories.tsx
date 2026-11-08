import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

import { PassReason } from "../components/deck/pass-reason";

const answer = (status: number) => {
  server.use(
    http.put("*/api/postings/:id/pass/reason", async ({ params, request }) =>
      status === 200
        ? HttpResponse.json({
            id: params.id,
            ...((await request.json()) as object),
          })
        : HttpResponse.json(
            {
              detail: "This posting changed since you saw it. Reload the deck.",
            },
            { status },
          ),
    ),
  );
};

const meta = {
  title: "Pages/Discover/Pass reason",
  component: PassReason,
  decorators: [withApp],
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Under the deck after a Pass: its reason in one tap, optional and never waited for. The next swipe or the undo takes it away; only Your search in numbers counts it.",
      },
    },
  },
  args: { postingId: "00000000-0000-4000-9000-000000000001" },
  beforeEach: () => {
    answer(200);
  },
} satisfies Meta<typeof PassReason>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Just after a Pass: the reasons, none chosen. */
export const AfterAPass: Story = {};

/** One tap: the reason is kept on the Pass. */
export const ReasonGiven: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("radio", { name: "Pay too low" }),
    );
  },
};

/** The Pass was undone elsewhere: the reason is not saved, and the page says so. */
export const NotSaved: Story = {
  beforeEach: () => {
    answer(409);
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("radio", { name: "Not my stack" }),
    );
  },
};
