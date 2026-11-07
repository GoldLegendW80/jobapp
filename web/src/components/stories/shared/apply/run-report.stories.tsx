import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunReport } from "@/components/shared/apply/run-report";
import {
  applicationIn,
  LONG_EVENTS,
  REFUSED_EVENTS,
  SUBMITTED_EVENTS,
} from "@/design-system/fixtures/shared-apply";

const meta = {
  title: "Shared/Apply/Run report",
  component: RunReport,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What happened to an application, in order, with the time of each step and how long it took: at the bottom of Your application on a posting.",
      },
    },
  },
  args: {
    application: applicationIn("submitted", {
      updated_at: "2026-11-05T08:07:13Z",
      submitted_at: "2026-11-05T08:07:13Z",
    }),
    events: SUBMITTED_EVENTS,
  },
} satisfies Meta<typeof RunReport>;

export default meta;
type Story = StoryObj<typeof meta>;

/** From the Apply click to the site's confirmation, with the approval in between. */
export const Submitted: Story = {
  name: "A submitted application",
};

/** The site showed errors after the click. */
export const RefusedBySite: Story = {
  name: "Refused by the site",
  args: {
    application: applicationIn("refused_by_site", {
      updated_at: "2026-11-05T08:07:13Z",
    }),
    events: REFUSED_EVENTS,
  },
};

/** Just started: one line, and the time so far. */
export const JustStarted: Story = {
  name: "Just started",
  args: {
    application: applicationIn("queued", {
      updated_at: "2026-11-05T08:00:03Z",
    }),
    events: SUBMITTED_EVENTS.slice(0, 1),
  },
};

/** A run tried several times, over more than an hour; a step this build does not know is said in words. */
export const LongRun: Story = {
  name: "A long run with retries",
  args: {
    application: applicationIn("submitted", {
      updated_at: "2026-11-05T09:15:44Z",
      submitted_at: "2026-11-05T09:15:44Z",
    }),
    events: LONG_EVENTS,
  },
};

/** On a phone, the times keep their column. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  args: { events: LONG_EVENTS },
};
