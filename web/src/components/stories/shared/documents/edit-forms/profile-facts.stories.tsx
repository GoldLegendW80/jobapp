import type { Meta, StoryObj } from "@storybook/react-vite";

import { ProfileFacts } from "@/components/shared/documents/edit-forms/profile-facts";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Documents/Profile facts",
  component: ProfileFacts,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <div className="max-w-xl">
        <Story />
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "What a document prints from the profile as it is (name, contact lines, employers, schools, languages) and where to change it, under Not printed in the document view.",
      },
    },
  },
  args: { profile: camilleProfile(), letter: false },
} satisfies Meta<typeof ProfileFacts>;

export default meta;
type Story = StoryObj<typeof meta>;

/** For a CV: every fact. */
export const ForACv: Story = { name: "For a CV" };

/** For a letter: the name and the contact lines. */
export const ForALetter: Story = {
  name: "For a letter",
  args: { letter: true },
};

/** A new profile: what is not there yet. */
export const EmptyProfile: Story = {
  name: "An empty profile",
  args: {
    profile: {
      identity: {
        ...camilleProfile().identity,
        full_name: null,
        email: null,
        phone: null,
        city: null,
        website: null,
        languages: [],
      },
      experience: { employers: [], education: [] },
    },
  },
};
