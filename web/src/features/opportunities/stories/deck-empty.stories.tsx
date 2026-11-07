import type { Meta, StoryObj } from "@storybook/react-vite";

import { POSTINGS } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeJobsApi } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";

import { DeckEmpty } from "../components/deck/deck-empty";

const meta = {
  title: "Pages/Discover/Empty deck",
  component: DeckEmpty,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The empty deck of Discover, said as its cause with the action that fixes it: done for today (a Rive celebration), no posting yet, or a profile without skills (a Rive search).",
      },
    },
  },
  args: { threshold: 70, filtered: false, motion: true },
  render: (args) => (
    <div className="max-w-lg">
      <DeckEmpty {...args} />
    </div>
  ),
} satisfies Meta<typeof DeckEmpty>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every posting above the threshold was swiped. */
export const DoneForToday: Story = {
  beforeEach: () => {
    fakeJobsApi(POSTINGS);
    fakeProfile();
  },
};

/** No posting at all yet: Find jobs. */
export const NoPostingsYet: Story = {
  beforeEach: () => {
    fakeJobsApi([]);
    fakeProfile();
  },
};

/** A profile without skills keeps every fit near 0. */
export const NoSkillsYet: Story = {
  beforeEach: () => {
    fakeJobsApi(POSTINGS);
    fakeProfile({});
  },
};

/** With a filter on, the threshold line stays. */
export const FilteredOut: Story = {
  args: { filtered: true, threshold: 82 },
};

/** With reduced motion, the icon stands where the scene plays. */
export const ReducedMotion: Story = {
  args: { motion: false },
  beforeEach: () => {
    fakeJobsApi(POSTINGS);
    fakeProfile();
  },
};
