import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";

import { SearchSetup } from "@/components/shared/jobs/search-setup/search-setup";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import {
  fakeSearchPermissions,
  findJobsSearch,
} from "@/design-system/fixtures/jobs-actions";
import { phoneScreen } from "@/design-system/fixtures/phone-screen";
import { withApp } from "@/design-system/with-app";
import { useSweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

/** The setup as Find jobs opens it, with the Jobs page's search runner. */
function Setup() {
  const runner = useSweepRunner();
  return (
    <SearchSetup
      runner={runner}
      onClose={() => undefined}
      onReturnFocus={() => undefined}
    />
  );
}

const meta = {
  title: "Shared/Jobs/Find jobs setup",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Find jobs: where to search and what you want, then Save and start search. A shadcn Dialog on a wide screen, a shadcn Drawer from the bottom on a phone (below 640 px, `useIsPhone`), dragged down to close. Opened by Find jobs on List and Map.",
      },
      story: { inline: false, height: "860px" },
    },
  },
  render: () => <Setup />,
  beforeEach: () => {
    fakeProfile({ ...camilleProfile(), search: findJobsSearch() });
    fakeSearchPermissions();
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** On a wide screen: a dialog in the middle, its header and footer in view, the middle scrolling. */
export const Dialog: Story = { name: "A dialog on a wide screen" };

/**
 * On a phone: a drawer from the bottom, with a grab handle, at most 92% of the screen high. The
 * story makes the page a phone: `window.matchMedia` answers the phone query (`useIsPhone` reads
 * it) and the page is 390 px wide (`phoneScreen`, design-system/fixtures).
 */
export const Drawer: Story = {
  name: "A drawer from the bottom on a phone",
  beforeEach: phoneScreen,
};

/** A first search: nothing listed, nothing asked for yet. */
export const FirstSearch: Story = {
  name: "A first search",
  beforeEach: () => {
    fakeProfile(null);
  },
};

/** While the settings load, the setup says so. */
export const Loading: Story = {
  name: "Loading your settings",
  beforeEach: () => {
    server.use(
      http.get("*/api/profile", async () => {
        await delay("infinite");
      }),
    );
  },
};

/** When the settings cannot be loaded: what failed, and Try again. */
export const LoadFails: Story = {
  name: "Settings could not be loaded",
  beforeEach: () => {
    server.use(
      http.get("*/api/profile", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** The same failure on a phone, in the drawer. */
export const LoadFailsOnAPhone: Story = {
  name: "Settings could not be loaded, on a phone",
  beforeEach: () => {
    server.use(
      http.get("*/api/profile", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
    return phoneScreen();
  },
};
