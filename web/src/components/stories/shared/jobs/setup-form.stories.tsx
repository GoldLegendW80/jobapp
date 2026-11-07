import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { screen, userEvent } from "storybook/test";

import { SetupForm } from "@/components/shared/jobs/search-setup/setup-form";
import { SetupSurface } from "@/components/shared/jobs/search-setup/setup-surface";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import {
  failSweepStart,
  fakeSearchPermissions,
  findJobsSearch,
} from "@/design-system/fixtures/jobs-actions";
import { withApp } from "@/design-system/with-app";
import { useSweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { useProfile } from "@/features/profile/api/use-profile";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

const nothing = () => undefined;

/** The form in the dialog Find jobs opens, on the profile the fake api answers. */
function Form() {
  const profile = useProfile({ retry: false });
  const runner = useSweepRunner();
  return (
    <SetupSurface onClose={nothing} onReturnFocus={nothing}>
      {profile.data ? (
        <SetupForm
          profile={profile.data}
          runner={runner}
          onClose={nothing}
          onBusyChange={nothing}
          onReload={async () => {
            await profile.refetch();
          }}
        />
      ) : null}
    </SetupSurface>
  );
}

/** Save and start search, the footer's primary action. */
async function start() {
  await userEvent.click(
    await screen.findByRole("button", { name: "Save and start search" }),
  );
}

/** Camille's search with one address of her own: saving asks no website's permission. */
function ownAddressOnly() {
  fakeProfile({
    ...camilleProfile(),
    search: {
      ...findJobsSearch(),
      interactive_sites: [
        {
          platform: "custom",
          url: "https://jobs.example.org/",
          enabled: true,
          mode: "llm_browser",
        },
      ],
    },
  });
}

const meta = {
  title: "Shared/Jobs/Setup form",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The two steps of Find jobs (where to search, what you want) and its footer, always in view: what went wrong, Cancel, and Save and start search.",
      },
      story: { inline: false, height: "860px" },
    },
  },
  render: () => <Form />,
  beforeEach: () => {
    fakeProfile({ ...camilleProfile(), search: findJobsSearch() });
    fakeSearchPermissions();
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Where to search, then what you want. */
export const Typical: Story = { name: "Where to search and what you want" };

/** More options: the words not wanted, the criteria, what the person aims for, instructions. */
export const MoreOptions: Story = {
  name: "More options open",
  play: async () => {
    await userEvent.click(
      await screen.findByRole("button", { name: "More options" }),
    );
  },
};

/** While it saves and starts, the form is locked and the button says so. */
export const Saving: Story = {
  name: "Saving and starting",
  beforeEach: () => {
    ownAddressOnly();
    server.use(
      http.put("*/api/profile/search", async () => {
        await delay("infinite");
      }),
    );
  },
  play: start,
};

/** The search did not start: the footer says why, the choices stay. */
export const StartFails: Story = {
  name: "The search did not start",
  beforeEach: () => {
    ownAddressOnly();
    failSweepStart();
  },
  play: start,
};

/** Changed in another tab: nothing is overwritten, Load current settings reviews it first. */
export const Conflict: Story = {
  name: "Changed in another tab",
  beforeEach: () => {
    ownAddressOnly();
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          { detail: "The profile changed since you loaded it." },
          { status: 409 },
        ),
      ),
    );
  },
  play: start,
};
