import type { Meta, StoryObj } from "@storybook/react-vite";

import { UnreadDocuments } from "@/components/shared/ingestion/run-progress/unread-documents";
import {
  cancelledRun,
  everyDocumentState,
  failedRun,
  partialRun,
} from "@/design-system/fixtures/shared-ingestion";

const meta = {
  title: "Shared/Ingestion/Unread documents",
  component: UnreadDocuments,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The selected documents an ended run did not read, each with why: in Run progress, under the figures of a finished, failed or cancelled run.",
      },
    },
  },
  args: { run: partialRun(), documents: everyDocumentState() },
} satisfies Meta<typeof UnreadDocuments>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A step that failed, a possible secret, a document deleted before its turn. */
export const EachWithItsReason: Story = { name: "Each with its reason" };

/** The run failed before the notes. */
export const AfterAFailure: Story = {
  name: "After the run failed",
  args: { run: failedRun() },
};

/** The run was cancelled before either document. */
export const AfterCancel: Story = {
  name: "After the run was cancelled",
  args: { run: cancelledRun() },
};
