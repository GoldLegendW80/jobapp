import type { Meta, StoryObj } from "@storybook/react-vite";

import { withApp } from "@/design-system/with-app";
import { SearchRow } from "@/components/shared/jobs/search-setup/search-row";
import {
  MAIN_SEARCH,
  REMOTE_SEARCH,
  WAITING_SEARCH,
} from "@/design-system/fixtures/saved-searches";

const meta = {
  title: "Shared/Jobs/Search row",
  component: SearchRow,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One search of Your searches in Find jobs: its name, what it searches in one line, its schedule and where it stands, with Run now and Edit, and Schedule, Duplicate, Rename and Delete in its menu. The main search is neither renamed nor deleted.",
      },
    },
  },
  decorators: [
    withApp,
    (Story) => (
      <ul className="max-w-2xl divide-y">
        <Story />
      </ul>
    ),
  ],
  args: {
    search: REMOTE_SEARCH,
    running: false,
    names: [],
    disabled: false,
    onRun: () => undefined,
    onEdit: () => undefined,
    onSchedule: () => undefined,
    onDuplicate: () => undefined,
    onRename: () => Promise.resolve(null),
    onDelete: () => undefined,
    onTakeBack: () => undefined,
    onOpenPostings: () => undefined,
  },
} satisfies Meta<typeof SearchRow>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A saved search on a schedule, never run yet. */
export const Scheduled: Story = { name: "A saved search on a schedule" };

/** The main search: its menu holds Duplicate alone. */
export const Main: Story = {
  name: "The main search",
  args: { search: MAIN_SEARCH },
};

/** A run asked while another runs: its place in the line, and Take back. */
export const Waiting: Story = {
  name: "Waiting in the line",
  args: { search: WAITING_SEARCH },
};

/** The search the running sweep runs. */
export const Running: Story = {
  name: "Running now",
  args: { running: true },
};

/** Its last run weeks ago, older than the last 20 searches: the date, and its new postings open in
 * the Jobs list. */
export const OldLastRun: Story = {
  name: "A last run weeks ago",
  args: {
    search: {
      ...REMOTE_SEARCH,
      last_run: {
        sweep_id: "0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa",
        status: "done",
        started_at: "2026-09-02T08:00:00Z",
        finished_at: "2026-09-02T08:10:00Z",
        new: 12,
      },
    },
  },
};
