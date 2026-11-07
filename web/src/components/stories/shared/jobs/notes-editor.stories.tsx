import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { NotesEditor } from "@/components/shared/jobs/job-editor/notes-editor";
import { EditableJob } from "@/design-system/fixtures/job-harnesses";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { WORKED_ON_POSTING } from "@/design-system/fixtures/posting-parts";
import { withApp } from "@/design-system/with-app";
import { NOTES_MAX } from "@/features/jobs/api/use-job-editor";
import { fakeEditableJob } from "@/test/jobs";

const meta = {
  title: "Shared/Jobs/Notes editor",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The person's notes on a posting, saved after a pause in typing or when the box loses focus, with a Retry when saving fails: in a posting's main column.",
      },
      story: { inline: false, height: "320px" },
    },
  },
  render: () => (
    <EditableJob id={POSTING_DETAIL.id}>
      {(job, save) => <NotesEditor job={job} save={save} />}
    </EditableJob>
  ),
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL);
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** No notes yet: the placeholder says what they are for. */
export const Empty: Story = { name: "No notes yet" };

/** Notes written on an earlier visit. */
export const Written: Story = {
  name: "Notes written",
  beforeEach: () => {
    fakeEditableJob(WORKED_ON_POSTING);
  },
};

/** Typing: the line beside the title says Saving, then Saved. */
export const Saving: Story = {
  name: "Saving as you type",
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL, { latencyMs: 4000 });
  },
  play: async ({ canvasElement }) => {
    await userEvent.type(
      await within(canvasElement).findByRole("textbox", { name: "Notes" }),
      "Ask about the on call rota.",
    );
  },
};

/** When saving fails the text stays, with what went wrong and Retry. */
export const SaveFails: Story = {
  name: "Saving fails",
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL, {
      fail: () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
    });
  },
  play: Saving.play,
};

/** Notes over the limit are not saved, and the help line says by how much. */
export const TooLong: Story = {
  name: "Too long",
  beforeEach: () => {
    fakeEditableJob({ ...POSTING_DETAIL, notes: "" });
  },
  play: async ({ canvasElement }) => {
    const box = await within(canvasElement).findByRole("textbox", {
      name: "Notes",
    });
    await userEvent.click(box);
    await userEvent.paste("a".repeat(NOTES_MAX + 1));
  },
};
