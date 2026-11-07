import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { VersionHistory } from "@/components/shared/documents/document-view/version-history";
import { fakeVersion } from "@/test/documents";

const ROWS = [
  fakeVersion({
    number: 3,
    origin: "chat",
    created_at: "2026-11-05T09:21:00Z",
  }),
  fakeVersion({
    number: 2,
    origin: "edited",
    created_at: "2026-11-05T08:40:00Z",
    approved_at: "2026-11-05T08:50:00Z",
  }),
  fakeVersion({
    number: 1,
    created_at: "2026-11-04T18:10:00Z",
    frozen_at: "2026-11-04T20:00:00Z",
    frozen_reason: "marked_sent",
  }),
];

const meta = {
  title: "Shared/Documents/Version history",
  component: VersionHistory,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The versions of a document, newest first, in the side rail of the document view: each opened on the page, restored into the draft or approved for applications.",
      },
    },
  },
  args: {
    rows: ROWS,
    loading: false,
    error: undefined,
    shown: 3,
    description: "An application sends the approved version, else the latest.",
    busy: false,
    onChoose: fn(),
    onRestore: fn(),
    onApprove: fn(),
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof VersionHistory>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Three versions: one approved, one sent. */
export const Typical: Story = { name: "Three versions" };

/** An earlier version open on the page. */
export const EarlierShown: Story = {
  name: "An earlier version shown",
  args: { shown: 1 },
};

/** While the versions are read. */
export const Loading: Story = { args: { rows: [], loading: true } };

/** They could not be read. */
export const CouldNotLoad: Story = {
  name: "Could not be loaded",
  args: { error: "The service is busy. Try again in a moment." },
};
