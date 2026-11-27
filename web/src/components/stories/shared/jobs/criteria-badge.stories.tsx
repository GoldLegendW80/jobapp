import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { CriteriaBadge } from "@/components/shared/jobs/criteria-badge";
import { withApp } from "@/design-system/with-app";
import { mainSearchOnly, server } from "@/test/server";

const OUTSIDE = {
  outside_criteria: ["contract", "work_mode", "older"],
  contract_type: "Freelance",
  work_mode: "remote",
  published_on: "2026-10-12",
  found_on: "2026-11-26",
};

/** The person's searches, each with its own age window in days. */
function searchesWith(...windows: number[]) {
  server.use(
    http.get("*/api/jobs/searches", () =>
      HttpResponse.json({
        ...mainSearchOnly,
        items: windows.map((days, index) => ({
          ...mainSearchOnly.items[0],
          id: index ? `saved-${String(index)}` : "main",
          main: index === 0,
          criteria: { days },
        })),
      }),
    ),
  );
}

const meta = {
  title: "Shared/Jobs/Criteria badge",
  component: CriteriaBadge,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Outside your criteria: what of a posting the search kept is outside the criteria of the search that found it (its contract, its work mode, older than the search's window), in words. On a row of the Jobs list and on a posting's header; nothing for a posting within them.",
      },
    },
  },
  args: { job: OUTSIDE },
  beforeEach: () => {
    searchesWith(30);
  },
} satisfies Meta<typeof CriteriaBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A freelance mission, remote, published 45 days before the search found it. */
export const Outside: Story = { name: "Outside three criteria" };

/** One criterion only: an internship where the search holds permanent contracts. */
export const OneCriterion: Story = {
  name: "Outside one criterion",
  args: {
    job: { ...OUTSIDE, outside_criteria: ["contract"], contract_type: "Stage" },
  },
};

/** The person's searches have different windows: the badge says the posting's own age. */
export const WindowsDiffer: Story = {
  name: "Searches with different windows",
  beforeEach: () => {
    searchesWith(30, 14);
  },
};

/** Within the criteria: no badge. */
export const Within: Story = {
  name: "Within: nothing",
  args: { job: { ...OUTSIDE, outside_criteria: [] } },
};

/** On a phone the badge wraps. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-64">
        <Story />
      </div>
    ),
  ],
};

/** Jobs J35: a required language the profile does not list, alone and after another criterion. */
export const Language: Story = {
  name: "Requires a language",
  args: {
    job: {
      ...OUTSIDE,
      outside_criteria: ["language"],
      languages_missing: ["German"],
    },
  },
};

export const LanguageAndCriterion: Story = {
  name: "Outside a criterion and requires a language",
  args: {
    job: {
      ...OUTSIDE,
      outside_criteria: ["work_mode", "language"],
      languages_missing: ["German", "Italian or Spanish"],
    },
  },
};
