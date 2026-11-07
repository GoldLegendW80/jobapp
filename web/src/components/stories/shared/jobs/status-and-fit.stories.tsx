import type { Meta, StoryObj } from "@storybook/react-vite";

import { StatusAndFit } from "@/components/shared/jobs/job-editor/status-and-fit";
import { EditableJob } from "@/design-system/fixtures/job-harnesses";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeEditableJob } from "@/test/jobs";

const meta = {
  title: "Shared/Jobs/Status and fit",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Where the posting stands for the person and its fit, both editable: a section of a posting's side rail, on its page and in the Jobs list's pane.",
      },
      story: { inline: false, height: "320px" },
    },
  },
  render: () => (
    <div className="max-w-sm">
      <EditableJob id={POSTING_DETAIL.id}>
        {(job, save) => <StatusAndFit job={job} save={save} />}
      </EditableJob>
    </div>
  ),
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL);
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** A CV ready, with its computed fit, at the rail's width. */
export const Typical: Story = { name: "In the side rail" };

/** Applied, with a fit set by hand. */
export const SetByHand: Story = {
  name: "Applied, fit set by hand",
  beforeEach: () => {
    fakeEditableJob({
      ...POSTING_DETAIL,
      status: "applied",
      applied_on: "2026-11-04",
      fit: 64,
      fit_overridden: true,
    });
  },
};
