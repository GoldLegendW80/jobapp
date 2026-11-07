import type { Meta, StoryObj } from "@storybook/react-vite";
import { screen, userEvent } from "storybook/test";

import { BulkNoteDialog } from "@/components/shared/jobs/jobs-actions/bulk-note-dialog";
import { fakeBulkUpdate } from "@/design-system/fixtures/jobs-actions";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Bulk note dialog",
  component: BulkNoteDialog,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "One note added at the end of the notes of every ticked posting: opened by Add note in the bulk bar of Jobs.",
      },
      story: { inline: false, height: "420px" },
    },
  },
  args: {
    ids: POSTINGS.slice(0, 3).map((job) => job.id),
    onClose: () => undefined,
    onDone: () => undefined,
  },
  beforeEach: () => {
    fakeBulkUpdate();
  },
} satisfies Meta<typeof BulkNoteDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

async function addNote() {
  await userEvent.type(
    await screen.findByLabelText("Note"),
    "Sent to Camille's former manager for a word.",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add note" }));
}

/** Three postings ticked: Add note waits for a note. */
export const Typical: Story = { name: "Write a note" };

/** The note added: a toast counts the postings. */
export const Added: Story = { name: "Note added", play: addNote };

/** When the server fails, nothing changes and the note stays typed. */
export const Fails: Story = {
  name: "Nothing was changed",
  beforeEach: () => {
    fakeBulkUpdate({ fail: true });
  },
  play: addNote,
};
