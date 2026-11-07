import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { ForgetDialog } from "@/components/shared/browser/forget-dialog";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Browser/Forget all logins",
  component: ForgetDialog,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Forget all logins, behind a confirmation that names what is lost: every website's login in the person's browser on the server, the session it ends, what stays. On Account's websites and beside a website's Sign in in Find jobs.",
      },
    },
  },
  args: {
    profile: { exists: true, size_bytes: 48_000_000, last_used_at: null },
    onForgotten: () => undefined,
  },
} satisfies Meta<typeof ForgetDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

const ask = async (canvasElement: HTMLElement) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", {
      name: "Forget all logins",
    }),
  );
};

/** The button, beside the websites the person signed in to. */
export const TheButton: Story = { name: "The button" };

/** The confirmation names what is deleted, its size, and what stays. */
export const Confirming: Story = {
  name: "Confirming",
  play: async ({ canvasElement }) => {
    await ask(canvasElement);
  },
};

/** A profile never written: the confirmation names no size. */
export const NothingStoredYet: Story = {
  name: "Nothing stored yet",
  args: { profile: { exists: false, size_bytes: 0, last_used_at: null } },
  play: async ({ canvasElement }) => {
    await ask(canvasElement);
  },
};

/** Refused, three times a day at most: the reason in the dialog, nothing deleted. */
export const Refused: Story = {
  name: "Refused",
  beforeEach: () => {
    server.use(
      http.delete("*/api/browser/profile", () =>
        HttpResponse.json(
          {
            detail:
              "You can forget your logins three times a day. Try again tomorrow.",
          },
          { status: 429 },
        ),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await ask(canvasElement);
    const dialog = await within(document.body).findByRole("alertdialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Forget all logins" }),
    );
  },
};
