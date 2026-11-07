import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { FitControl } from "@/components/shared/jobs/job-editor/fit-control";
import { EditableJob } from "@/design-system/fixtures/job-harnesses";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeEditableJob } from "@/test/jobs";

const meta = {
  title: "Shared/Jobs/Fit control",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The fit set by hand, 0 to 100, kept by every rescore, and Use computed to give it back: in a posting's Status and fit section.",
      },
      story: { inline: false, height: "200px" },
    },
  },
  render: () => (
    <div className="max-w-md">
      <EditableJob id={POSTING_DETAIL.id}>
        {(job, save) => <FitControl job={job} save={save} />}
      </EditableJob>
    </div>
  ),
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL);
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The fit the app computed from the posting and the skills. */
export const Computed: Story = { name: "A computed fit" };

/** A fit set by hand says so, and offers the computed one back. */
export const SetByHand: Story = {
  name: "A fit set by hand",
  beforeEach: () => {
    fakeEditableJob({ ...POSTING_DETAIL, fit: 64, fit_overridden: true });
  },
};

/** A posting not scored yet: the box is empty. */
export const NotScored: Story = {
  name: "Not scored yet",
  beforeEach: () => {
    fakeEditableJob({ ...POSTING_DETAIL, fit: null });
  },
};

/** Typing a fit and Set by hand saves it, with a toast. */
export const SetAFit: Story = {
  name: "Set a fit by hand",
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const input = await canvas.findByLabelText("Fit");
    await userEvent.clear(input);
    await userEvent.type(input, "70");
    await userEvent.click(canvas.getByRole("button", { name: "Set by hand" }));
  },
};

/** A number outside 0 to 100 cannot be set. */
export const OutOfRange: Story = {
  name: "A fit out of range",
  play: async ({ canvasElement }) => {
    const input = await within(canvasElement).findByLabelText("Fit");
    await userEvent.clear(input);
    await userEvent.type(input, "140");
  },
};

/** When saving fails, the fit goes back and a toast says why. */
export const SaveFails: Story = {
  name: "Saving fails",
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL, {
      fail: () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
    });
  },
  play: SetAFit.play,
};
