import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { StatusControl } from "@/components/shared/jobs/job-editor/status-control";
import { EditableJob } from "@/design-system/fixtures/job-harnesses";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeEditableJob } from "@/test/jobs";

const meta = {
  title: "Shared/Jobs/Status control",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The posting's status, saved as soon as it is picked; Applied asks first, since it freezes the files: in a posting's Status and fit section.",
      },
      story: { inline: false, height: "320px" },
    },
  },
  render: () => (
    <div className="max-w-md">
      <EditableJob id={POSTING_DETAIL.id}>
        {(job, save) => <StatusControl job={job} save={save} />}
      </EditableJob>
    </div>
  ),
  beforeEach: () => {
    fakeEditableJob({ ...POSTING_DETAIL, status: "shortlisted" });
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** A shortlisted posting. */
export const Typical: Story = { name: "A shortlisted posting" };

/** Picking a status saves it at once, with a toast. */
export const PickAStatus: Story = {
  name: "Pick a status",
  play: async ({ canvasElement }) => {
    await userEvent.selectOptions(
      await within(canvasElement).findByLabelText("Status"),
      "staged",
    );
  },
};

/** Applied asks first: the files of an applied posting are never regenerated. */
export const MarkAsApplied: Story = {
  name: "Mark as applied asks first",
  play: async ({ canvasElement }) => {
    await userEvent.selectOptions(
      await within(canvasElement).findByLabelText("Status"),
      "applied",
    );
  },
};

/** A status a newer api sends stays shown, first in the list. */
export const UnknownStatus: Story = {
  name: "A status this build does not know",
  beforeEach: () => {
    fakeEditableJob({ ...POSTING_DETAIL, status: "offer_received" });
  },
};

/** When saving fails, the status goes back and a toast says why. */
export const SaveFails: Story = {
  name: "Saving fails",
  beforeEach: () => {
    fakeEditableJob(
      { ...POSTING_DETAIL, status: "shortlisted" },
      {
        fail: () =>
          HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      },
    );
  },
  play: PickAStatus.play,
};
