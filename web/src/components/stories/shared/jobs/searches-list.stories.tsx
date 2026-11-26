import type { Meta, StoryObj } from "@storybook/react-vite";

import { SearchesList } from "@/components/shared/jobs/search-setup/searches-list";
import {
  REMOTE_SEARCH,
  WAITING_SEARCH,
} from "@/design-system/fixtures/saved-searches";
import { withApp } from "@/design-system/with-app";
import { fakeSearches } from "@/test/searches";

const RUNNER = {
  running: null,
  starting: false,
  start: () => Promise.resolve(),
  follow: () => undefined,
  summary: null,
  showSummary: () => undefined,
};

const meta = {
  title: "Shared/Jobs/Your searches",
  component: SearchesList,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The first screen of Find jobs: the main search, then the saved ones, with New search and Run all above them, and the one sentence that names what every search shares, with its link to Settings, Search.",
      },
      story: { inline: false, height: "640px" },
    },
  },
  args: {
    runner: RUNNER,
    onEdit: () => undefined,
    onNew: () => undefined,
    onStarted: () => undefined,
  },
} satisfies Meta<typeof SearchesList>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A new account: the main search alone. */
export const MainOnly: Story = {
  name: "The main search alone",
  beforeEach: () => {
    fakeSearches();
  },
};

/** Two saved searches beside the main one, one of them waiting in the line. */
export const Several: Story = {
  name: "Several searches, one in the line",
  beforeEach: () => {
    fakeSearches({ saved: [REMOTE_SEARCH, WAITING_SEARCH] });
  },
};
