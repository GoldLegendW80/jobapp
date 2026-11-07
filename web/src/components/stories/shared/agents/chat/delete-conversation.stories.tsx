import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { fn, userEvent, within } from "storybook/test";

import { DeleteConversation } from "@/components/shared/agents/chat/delete-conversation";
import { THREAD } from "@/design-system/fixtures/shared-agents";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Agents/Delete conversation",
  component: DeleteConversation,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <div className="max-w-md rounded-xl border bg-background">
        <Story />
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The bar above a conversation in the Chat panel of a posting: Delete conversation asks in a dialog, Delete or Keep. Nothing is deleted on the first click.",
      },
    },
  },
  args: { jobId: THREAD.job_id ?? "", threadId: THREAD.id, onDeleted: fn() },
  beforeEach: () => {
    server.use(
      http.delete("*/api/agents/threads/:id", async () => {
        await delay(500);
        return new HttpResponse(null, { status: 204 });
      }),
    );
  },
} satisfies Meta<typeof DeleteConversation>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The button, before it is clicked. */
export const DeleteAConversation: Story = { name: "Delete a conversation" };

/** The first click only asks: Delete removes it, Keep goes back. */
export const ConfirmTheDeletion: Story = {
  name: "Confirm the deletion",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", {
        name: "Delete conversation",
      }),
    );
  },
};

/** The api refuses: the toast says why and the question stays. */
export const DeletionFails: Story = {
  name: "Deletion fails",
  beforeEach: () => {
    server.use(
      http.delete("*/api/agents/threads/:id", () =>
        HttpResponse.json(
          { detail: "The conversation could not be deleted. Try again." },
          { status: 500 },
        ),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", {
        name: "Delete conversation",
      }),
    );
    // The dialog opens over the page, outside the story's frame.
    const dialog = await within(canvasElement.ownerDocument.body).findByRole(
      "alertdialog",
      { name: "Delete this conversation?" },
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Delete" }),
    );
  },
};
