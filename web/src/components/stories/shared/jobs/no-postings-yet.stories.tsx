import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { NoPostingsYet } from "@/components/shared/jobs/no-postings-yet";
import { FindJobsSetup } from "@/components/shared/jobs/find-jobs";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import { WithFinder } from "@/design-system/fixtures/job-harnesses";
import { fakeSearchPermissions } from "@/design-system/fixtures/jobs-actions";
import { withApp } from "@/design-system/with-app";
import { fakeProfile } from "@/test/profile";

/** The empty view with the setup its Find jobs opens, as the Jobs page holds both. */
function Empty() {
  return (
    <WithFinder>
      {(finder) => (
        <>
          <NoPostingsYet finder={finder} />
          <FindJobsSetup finder={finder} />
        </>
      )}
    </WithFinder>
  );
}

const meta = {
  title: "Shared/Jobs/No postings yet",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "What List and Map show before the first posting, with no filter set: how to get postings, Find jobs or Add a posting by hand.",
      },
      story: { inline: false, height: "760px" },
    },
  },
  beforeEach: () => {
    fakeProfile(camilleProfile());
    fakeSearchPermissions();
  },
  render: () => <Empty />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Find jobs, the primary action, and adding one by hand. */
export const Typical: Story = { name: "No postings yet" };

/** Find jobs opens its setup. */
export const FindJobs: Story = {
  name: "Open Find jobs",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Find jobs" }),
    );
  },
};

/** On a phone the two actions stack. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <Empty />
    </div>
  ),
};
