import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { screen, userEvent, within } from "storybook/test";

import { BulkBar } from "@/components/shared/jobs/jobs-actions/bulk-bar";
import { fakeBulkUpdate } from "@/design-system/fixtures/jobs-actions";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { BULK_MAX } from "@/features/jobs/api/jobs-api";

/** The bar over `ids`, cleared by its Clear and by the actions that end. */
function Bar({
  ids: initial = POSTINGS.slice(0, 3).map((job) => job.id),
  selectingAll = false,
  cappedFrom = null,
}: {
  ids?: string[];
  selectingAll?: boolean;
  cappedFrom?: number | null;
}) {
  const [ids, setIds] = useState(initial);
  return (
    <BulkBar
      selection={{
        ids,
        selectingAll,
        cappedFrom,
        clear: () => {
          setIds([]);
        },
      }}
    />
  );
}

const meta = {
  title: "Shared/Jobs/Bulk bar",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "What can be done to the postings ticked in the list or on the map: set a status with Undo after, Apply, Like, add a note. It sticks to the bottom of the Jobs views while postings are ticked.",
      },
      story: { inline: false, height: "360px" },
    },
  },
  beforeEach: () => {
    fakeBulkUpdate();
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Three postings ticked. */
export const Typical: Story = {
  name: "Postings ticked",
  render: () => <Bar />,
};

/** Set status saves at once, with Undo in the toast for a while. */
export const SetAStatus: Story = {
  name: "Set a status",
  render: () => <Bar />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(
      canvas.getByLabelText("New status"),
      "shortlisted",
    );
    await userEvent.click(canvas.getByRole("button", { name: "Set status" }));
    await screen.findByText(/set to Shortlisted/);
  },
};

/** Applied asks first, and says how long the undo lasts. */
export const MarkAsApplied: Story = {
  name: "Mark as applied asks first",
  render: () => <Bar />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(
      canvas.getByLabelText("New status"),
      "applied",
    );
    await userEvent.click(canvas.getByRole("button", { name: "Set status" }));
  },
};

/** While every posting of the view is gathered, the count waits. */
export const SelectingAll: Story = {
  name: "Selecting every posting",
  render: () => <Bar ids={[]} selectingAll />,
};

/** More than a bulk action takes: only the first 500 are ticked, and the bar says so. */
export const Capped: Story = {
  name: "Only the first 500",
  render: () => (
    <Bar
      ids={Array.from(
        { length: BULK_MAX },
        (_, index) => `posting-${String(index)}`,
      )}
      cappedFrom={812}
    />
  ),
};

/** On a phone the actions wrap under the count. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <Bar />
    </div>
  ),
};
