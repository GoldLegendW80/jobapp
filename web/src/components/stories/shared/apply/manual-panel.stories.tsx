import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { ManualPanel } from "@/components/shared/apply/manual-panel";
import type { ManualStatus } from "@/features/apply/api/manual-api";
import { withApp } from "@/design-system/with-app";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";
import { fakeApplication } from "@/test/apply";
import { server } from "@/test/server";

const manual = (state: string, needs: string | null = null) =>
  fakeApplication(state, { mode: "manual", needs });

/** GET /api/applications/{id}/manual: the person's session to send it themselves. */
function session(over: Partial<ManualStatus> = {}) {
  const status: ManualStatus = {
    state: "filling",
    phase: "open",
    position: null,
    documents: ["cv", "letter"],
    confirmation_seen: false,
    chooser: false,
    idle_left_s: 240,
    idle_warning: false,
    busy: null,
    last: null,
    warnings: [],
    ...over,
  };
  server.use(
    http.get("*/api/applications/:id/manual", () => HttpResponse.json(status)),
    http.post("*/api/applications/:id/manual/open", () =>
      HttpResponse.json(status, { status: 202 }),
    ),
  );
}

const meta = {
  title: "Shared/Apply/Apply myself panel",
  component: ManualPanel,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Apply myself on a job's Your application: the form opens in the person's own browser, live here with the keyboard and the mouse theirs; the app fills their details or attaches a document on request, and records the application once they sent it. The app never clicks Submit here.",
      },
    },
  },
  args: {
    application: manual("filling"),
    onChanged: () => undefined,
    onFailed: () => undefined,
  },
  beforeEach: () => {
    liveRoutes();
    browserHolds(
      lease({ purpose: "apply_manual", automation: false, max_s: 1800 }),
    );
    session();
    return fakeLiveScreens({ pages: ["form"] });
  },
} satisfies Meta<typeof ManualPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The form open in the person's browser, with Fill my details and the documents to attach. */
export const YourBrowserIsOpen: Story = { name: "Your browser is open" };

/** Another lease holds the browser: the place in line. */
export const WaitingForYourBrowser: Story = {
  name: "Waiting for your browser",
  args: { application: manual("queued") },
  beforeEach: () => {
    session({ state: "queued", phase: "waiting", position: 2 });
  },
};

/** The page shows a confirmation: Record as applied comes first. */
export const TheSiteConfirmed: Story = {
  name: "The site confirmed",
  beforeEach: () => {
    session({
      confirmation_seen: true,
      last: { action: "fill_details", outcome: "done" },
    });
  },
};

/** Nobody touched the browser for a while: it closes in a minute unless they click or type. */
export const AboutToClose: Story = {
  name: "About to close",
  beforeEach: () => {
    session({ idle_warning: true, idle_left_s: 60 });
  },
};

/** The session ended before the app saw a confirmation: I submitted it, or open it again. */
export const SessionEnded: Story = {
  name: "Session ended",
  args: { application: manual("queued", "session_ended") },
  beforeEach: () => {
    session({ state: "queued", phase: "ended" });
  },
};

/** Sent by the person: what the app kept. */
export const Sent: Story = {
  name: "Sent",
  args: { application: manual("submitted") },
};

/** On a phone the buttons take the width, one under the other. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
