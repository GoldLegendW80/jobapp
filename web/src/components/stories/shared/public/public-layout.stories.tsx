import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  PublicLayout,
  Section,
} from "@/components/shared/public/public-layout";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Public/Public layout",
  component: PublicLayout,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    layout: "fullscreen",
    at: "/privacy",
    docs: {
      description: {
        component:
          "The frame of the public pages (home, privacy, terms): the app's mark and one navigation to all of them and to sign in, then one readable column. It reads no session and asks search engines not to index the pages.",
      },
    },
  },
  args: {
    title: "Privacy",
    children: (
      <>
        <h1 className="text-3xl font-semibold tracking-tight">Privacy</h1>
        <p className="text-muted-foreground">
          This app runs on one small computer at its owner&apos;s home, for its
          owner and a few invited friends.
        </p>
        <Section id="story-what" title="What the app keeps">
          <p>
            Your profile, the postings it found for you, the documents it wrote
            and the applications you approved. Nothing is sold or shared.
          </p>
        </Section>
        <Section id="story-delete" title="Deleting your account">
          <p>
            Ask the owner, or delete it yourself on the Account page: every
            document and application goes with it.
          </p>
        </Section>
      </>
    ),
  },
} satisfies Meta<typeof PublicLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A text page with its sections, Privacy current in the navigation. */
export const ATextPage: Story = { name: "A text page with sections" };

/** The home page: Home current, no tab title of its own. */
export const HomePage: Story = {
  name: "Home page",
  parameters: { at: "/" },
  args: {
    title: "",
    children: (
      <>
        <h1 className="text-3xl font-semibold tracking-tight">
          Find postings, write the letters, apply with one click
        </h1>
        <p className="text-muted-foreground">
          A private job search assistant. Nothing is sent without your approval.
        </p>
      </>
    ),
  },
};

/** On a phone the navigation wraps under the mark. */
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
