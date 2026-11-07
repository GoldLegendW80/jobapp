import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { HeaderActions } from "@/components/shared/jobs/jobs-actions/header-actions";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import { WithFinder } from "@/design-system/fixtures/job-harnesses";
import {
  fakeSearchPermissions,
  findJobsSearch,
} from "@/design-system/fixtures/jobs-actions";
import { withApp } from "@/design-system/with-app";
import { fakeProfile } from "@/test/profile";

/** The actions as the Jobs header lays them out. */
function Actions({ empty = false }: { empty?: boolean }) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <WithFinder>
        {(finder) => (
          <HeaderActions finder={finder} selected={[]} empty={empty} />
        )}
      </WithFinder>
    </div>
  );
}

const meta = {
  title: "Shared/Jobs/Header actions",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The actions of the Jobs header, the same on List and Map: Find jobs first, Add a posting, then More actions; with the Find jobs setup they open.",
      },
      story: { inline: false, height: "760px" },
    },
  },
  beforeEach: () => {
    fakeProfile({ ...camilleProfile(), search: findJobsSearch() });
    fakeSearchPermissions();
  },
  render: () => <Actions />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Find jobs, the one primary action, then the others. */
export const Typical: Story = { name: "With postings" };

/** No posting at all: the empty list holds Find jobs and Add a posting, the header only the menu. */
export const NoPosting: Story = {
  name: "No posting yet",
  render: () => <Actions empty />,
};

/** Find jobs opens its setup. */
export const FindJobs: Story = {
  name: "Open Find jobs",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Find jobs" }),
    );
  },
};
