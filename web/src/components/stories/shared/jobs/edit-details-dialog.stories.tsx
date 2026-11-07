import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";
import { screen, userEvent } from "storybook/test";

import { EditDetailsDialog } from "@/components/shared/jobs/job-editor/edit-details-dialog";
import { EditableJob } from "@/design-system/fixtures/job-harnesses";
import { phoneScreen } from "@/design-system/fixtures/phone-screen";
import { POSTING_DETAIL, POSTINGS } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeEditableJob } from "@/test/jobs";

const meta = {
  title: "Shared/Jobs/Edit details dialog",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Company, title, location, contract type, work mode, pay and link of a posting, saving only what changed: opened by Edit details in a posting's header.",
      },
      story: { inline: false, height: "820px" },
    },
  },
  render: () => (
    <EditableJob id={POSTING_DETAIL.id}>
      {(job, save) => (
        <EditDetailsDialog job={job} save={save} onClose={() => undefined} />
      )}
    </EditableJob>
  ),
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL);
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The posting's details, ready to change. */
export const Typical: Story = { name: "Edit the details" };

/** Without a company or a title there is nothing to save. */
export const Missing: Story = {
  name: "Company missing",
  play: async () => {
    await userEvent.clear(await screen.findByLabelText("Company"));
  },
};

/** A pay whose lowest figure is above the highest is not a pay. */
export const PayInvalid: Story = {
  name: "A pay that does not add up",
  play: async () => {
    const lowest = await screen.findByLabelText("Lowest pay");
    await userEvent.clear(lowest);
    await userEvent.type(lowest, "90000");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
  },
};

/** Details that match another posting link to it instead of making a twin. */
export const Duplicate: Story = {
  name: "The same details as another posting",
  beforeEach: () => {
    const other = POSTINGS[1];
    fakeEditableJob(POSTING_DETAIL, {
      fail: () =>
        HttpResponse.json(
          {
            detail: "This posting is already in your list.",
            existing_id: other?.id,
            existing_slug: other?.slug,
          },
          { status: 409 },
        ),
    });
  },
  play: async () => {
    const title = await screen.findByLabelText("Title");
    await userEvent.clear(title);
    await userEvent.type(title, "Analytics engineer");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
  },
};

/** While it saves, the buttons wait. */
export const Saving: Story = {
  name: "Saving",
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL, { latencyMs: 10_000 });
  },
  play: async () => {
    const location = await screen.findByLabelText("Location");
    await userEvent.type(location, ", France");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
  },
};

/** On a phone the dialog takes the width and scrolls. */
export const OnAPhone: Story = {
  name: "On a phone",
  beforeEach: phoneScreen,
};
