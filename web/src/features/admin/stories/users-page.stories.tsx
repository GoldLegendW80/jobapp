import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { expect, screen, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  ACCOUNTS,
  INVITES,
  invitesPage,
  manyInvites,
  usersPage,
} from "@/design-system/fixtures/admin-users";
import type { AdminInvite, AdminUser } from "@/features/admin/api/admin-api";
import { admin, fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

type Answer<T> = T[] | "error" | "wait";

/** The two lists of the page: their items, a server error, or an answer that never comes. */
function fakeLists(users: Answer<AdminUser>, invites: Answer<AdminInvite>) {
  server.use(
    http.get("*/api/admin/users", () => answer(users, usersPage)),
    http.get("*/api/admin/invites", () => answer(invites, invitesPage)),
  );
}

async function answer<T>(value: Answer<T>, page: (items: T[]) => object) {
  if (value === "wait") await delay("infinite");
  if (value === "error" || value === "wait")
    return HttpResponse.json({ detail: "Server error" }, { status: 500 });
  return HttpResponse.json(page(value));
}

const meta = {
  title: "Pages/Admin/Users",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The admin home: who can sign in, and the invite and password reset links. Route /admin/users, for administrators only.",
      },
    },
  },
  render: () => <AppAt path="/admin/users" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an administrator, I want to see every account with its role, status and sessions, and every link I sent with its status, so that I know who can sign in and which links still work. */
export const ManageAccountsAndInvites: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists(ACCOUNTS, INVITES);
  },
};

/** As an administrator, I want to create a link for a new person from the page, so that I can send it to them myself. */
export const InviteSomeone: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists(ACCOUNTS, INVITES);
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      await canvas.findByRole("button", { name: "Invite someone" }),
    );
    await screen.findByRole("dialog", { name: "Invite someone" });
  },
};

/** As an administrator, I want the new link shown once in its box with Copy link beside it, so that I copy it and send it right away. */
export const CopyTheNewLink: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists(ACCOUNTS, INVITES);
    server.use(
      http.post("*/api/admin/invites", () =>
        HttpResponse.json(
          {
            invite: { ...INVITES[0], email: "e2e-new-friend@example.org" },
            path: `/invite/${"Q2xvdWRzX2FuZF9yaXZlcnNfb2ZfaW52ZW50ZWRfZGF0YQ".slice(0, 43)}`,
          },
          { status: 201 },
        ),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Invite someone",
      }),
    );
    const dialog = within(
      await screen.findByRole("dialog", { name: "Invite someone" }),
    );
    await userEvent.type(
      dialog.getByLabelText("Email"),
      "e2e-new-friend@example.org",
    );
    await userEvent.click(dialog.getByRole("button", { name: "Create link" }));
    await screen.findByRole("dialog", { name: "Invite link ready" });
  },
};

/** As an administrator who sent many links, I want them a page at a time with where I am in the list, so that the page stays short and I can go back and forth. */
export const PageThroughManyLinks: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists(ACCOUNTS, INVITES);
    const many = manyInvites(45);
    server.use(
      http.get("*/api/admin/invites", ({ request }) => {
        const offset = Number(
          new URL(request.url).searchParams.get("offset") ?? "0",
        );
        return HttpResponse.json({
          ...invitesPage(many.slice(offset, offset + 20)),
          total: many.length,
          offset,
        });
      }),
    );
  },
};

/** As an administrator, I want a link without an e-mail address refused next to the field, so that I never create a link for nobody. */
export const InviteWithoutAnEmail: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists(ACCOUNTS, INVITES);
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      await canvas.findByRole("button", { name: "Invite someone" }),
    );
    const dialog = within(
      await screen.findByRole("dialog", { name: "Invite someone" }),
    );
    await userEvent.click(dialog.getByRole("button", { name: "Create link" }));
    await expect(
      await dialog.findByText("Enter an email address."),
    ).toBeVisible();
  },
};

/** As an administrator, I want to confirm before I disable an account, so that nobody is signed out everywhere by a slip of the mouse. */
export const ConfirmBeforeDisabling: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists(ACCOUNTS, INVITES);
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      await canvas.findByRole("button", { name: "Actions for Tomás Okafor" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Disable" }),
    );
    await screen.findByRole("alertdialog", { name: "Disable Tomás Okafor?" });
  },
};

/** As an administrator of a new install, I want the empty lists to say what will appear in them, so that I know where people show up once I invite them. */
export const NothingListedYet: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists([], []);
  },
};

/** As an administrator, I want to know when the lists could not be loaded and try again, so that I never mistake an error for an empty install. */
export const ListsUnavailable: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists("error", "error");
  },
};

/** As an administrator, I want to see that the lists are on their way, and to be told when the server has not answered within five seconds, so that I wait or retry instead of reloading. */
export const LoadingTheLists: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeLists("wait", "wait");
  },
};

/** As an invited friend who types the admin address, I want the page not found, so that I am told plainly this is not for me. */
export const MemberSeesNotFound: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeLists(ACCOUNTS, INVITES);
  },
};

/** As an administrator whose session ended, I want to land on the sign in page, so that I can sign in and come back here. */
export const SignedOutGoesToSignIn: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As an administrator on my phone, I want each account and link as a card of its own, so that I can manage them without scrolling sideways. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(admin);
    fakeLists(ACCOUNTS, INVITES);
  },
};
