import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { EditDetailsButton } from "@/components/shared/jobs/job-editor/edit-details-button";
import { EditableJob } from "@/design-system/fixtures/job-harnesses";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeEditableJob } from "@/test/jobs";

const meta = {
  title: "Shared/Jobs/Edit details button",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Edit details, an outline button among a posting's header actions, which opens the details dialog.",
      },
      story: { inline: false, height: "820px" },
    },
  },
  render: () => (
    <EditableJob id={POSTING_DETAIL.id}>
      {(job, save) => <EditDetailsButton job={job} save={save} />}
    </EditableJob>
  ),
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL);
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The button, as the header shows it. */
export const Typical: Story = { name: "In a posting's header" };

/** A click opens the dialog; saving closes it with a toast. */
export const Open: Story = {
  name: "Edit the details",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Edit details",
      }),
    );
  },
};
