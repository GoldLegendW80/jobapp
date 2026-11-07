import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import { fakeSession, member } from "@/test/auth";

const meta = {
  title: "Pages/Not found",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "Any address the app does not know, inside the signed in shell. An admin page opened by a member shows it too, as the api answers 404 to members.",
      },
    },
  },
  render: () => <AppAt path="/no-such-page" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker who followed an old or mistyped link, I want to be told the page does not exist and be given a way home, so that I am not stuck. */
export const OpenAnUnknownAddress: Story = {
  beforeEach: () => {
    fakeSession(member);
  },
};

/** As a job seeker who is not an administrator, I want an admin address to look like any unknown page, so that the app does not tell me what it hides. */
export const MemberOpensAnAdminPage: Story = {
  render: () => <AppAt path="/admin/users" />,
  beforeEach: () => {
    fakeSession(member);
  },
};

/** As a visitor who is not signed in, I want an unknown address to ask me to sign in first, so that the app says nothing about its pages to strangers. */
export const SignedOutGoesToSignIn: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As a job seeker on my phone, I want the message and the way home above the tab bar, so that one tap takes me back. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(member);
  },
};
