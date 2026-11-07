import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn, userEvent, within } from "storybook/test";

import { LikeButton } from "@/components/shared/apply/like-button";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { fakeLikes } from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";

const IDS = POSTINGS.slice(0, 3).map((job) => job.id);

const meta = {
  title: "Shared/Apply/Like button",
  component: LikeButton,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: "/jobs",
    docs: {
      description: {
        component:
          "Like on the Jobs list's bulk bar: one Like per selected posting. In Review first a Like fills the form and waits for approval; in Full auto the Like is the approval. A toast says which, with a link to Applications.",
      },
    },
  },
  args: { ids: IDS, onDone: fn() },
  beforeEach: () => {
    fakeLikes();
  },
} satisfies Meta<typeof LikeButton>;

export default meta;
type Story = StoryObj<typeof meta>;

const like = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", { name: "Like" }),
  );
};

/** Three postings selected: the button likes them all. */
export const ThreeSelected: Story = {
  name: "Three postings selected",
};

/** Nothing selected: the button waits. */
export const NothingSelected: Story = {
  name: "Nothing selected",
  args: { ids: [] },
};

/** In Review first: the assistant fills each form, then each waits for approval in Applications. */
export const LikedInReviewFirst: Story = {
  name: "Liked in Review first",
  play: like,
};

/** In Full auto: the Like is the approval, each form is submitted after the hold when every check passes. */
export const LikedInFullAuto: Story = {
  name: "Liked in Full auto",
  beforeEach: () => {
    fakeLikes({ mode: "full_auto" });
  },
  play: like,
};

/** Full auto rehearses first: nothing is submitted, the app asks "Does this look right?". */
export const LikedWhileRehearsing: Story = {
  name: "Liked while Full auto rehearses",
  beforeEach: () => {
    fakeLikes({ mode: "full_auto", dryRun: true });
  },
  play: like,
};

/** One posting is refused with the api's sentence; the others still go. */
export const SomeRefused: Story = {
  name: "One posting is refused",
  beforeEach: () => {
    fakeLikes({
      refused: {
        [IDS[1] ?? ""]: "You already applied to this company this month.",
      },
    });
  },
  play: like,
};

/** While the Likes go out, the button spins and waits. */
export const Liking: Story = {
  name: "Liking",
  beforeEach: () => {
    fakeLikes({ wait: "infinite" });
  },
  play: like,
};
