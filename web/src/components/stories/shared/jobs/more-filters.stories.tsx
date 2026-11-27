import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import {
  ApplicationMenu,
  CriteriaFilter,
  SearchFilter,
  FilterPresets,
  PayFilter,
  PlaceFilter,
  PublishedFilter,
  WebsiteMenu,
  WorkModeMenu,
} from "@/components/shared/jobs/filters/more-filters";
import { FACETS } from "@/design-system/fixtures/job-filters";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import {
  MAIN_SEARCH,
  REMOTE_SEARCH,
  WAITING_SEARCH,
} from "@/design-system/fixtures/saved-searches";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/More filters",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The filters of the Filters panel beyond status, contract, fit, found and company: two presets, the place, the work mode, the criteria of the search, the website, the published date, the pay and the application. Each one is in the address and has its chip.",
      },
      story: { inline: false, height: "420px" },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Found today, and Hide skipped and closed: one click each, the other filters kept. */
export const Presets: Story = {
  render: () => (
    <WithFilters>{(props) => <FilterPresets {...props} />}</WithFilters>
  ),
};

/** The places of the person's postings with their counts, and another one typed in. */
export const Place: Story = {
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, places: ["Saint-Denis"] }}>
      {(props) => (
        <PlaceFilter {...props} facets={FACETS.places} loading={false} />
      )}
    </WithFilters>
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /^Place/ }),
    );
  },
};

/** The places while the first answer loads. */
export const PlaceLoading: Story = {
  name: "Place, loading",
  render: () => (
    <WithFilters>
      {(props) => <PlaceFilter {...props} facets={undefined} loading />}
    </WithFilters>
  ),
};

/** A stated mode, or none stated. */
export const WorkMode: Story = {
  name: "Work mode",
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, workModes: ["remote"] }}>
      {(props) => <WorkModeMenu {...props} />}
    </WithFilters>
  ),
};

/** The websites the postings came from, in words; a posting found on two counts on each. */
export const Website: Story = {
  render: () => (
    <WithFilters>
      {(props) => (
        <WebsiteMenu {...props} facets={FACETS.sources} loading={false} />
      )}
    </WithFilters>
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /^Website/ }),
    );
  },
};

/** Published in the last 7 days, the postings without a date kept. */
export const Published: Story = {
  render: () => (
    <WithFilters
      initial={{ ...EMPTY_FILTERS, publishedWithin: 7, publishedUndated: true }}
    >
      {(props) => <PublishedFilter {...props} />}
    </WithFilters>
  ),
};

/** The postings one of the person's searches added, offered once she has a saved search
 * (jobs J33b). */
export const Search: Story = {
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, search: REMOTE_SEARCH.id }}>
      {(props) => (
        <SearchFilter
          {...props}
          searches={[MAIN_SEARCH, REMOTE_SEARCH, WAITING_SEARCH]}
        />
      )}
    </WithFilters>
  ),
};

/** A search deleted since the address was made: named so, and changed or cleared here. */
export const DeletedSearch: Story = {
  name: "Search deleted since",
  render: () => (
    <WithFilters
      initial={{
        ...EMPTY_FILTERS,
        search: "00000000-0000-4000-8000-000000000009",
      }}
    >
      {(props) => (
        <SearchFilter {...props} searches={[MAIN_SEARCH, REMOTE_SEARCH]} />
      )}
    </WithFilters>
  ),
};

/** Every posting by default; those within the criteria of their search, or those kept outside
 * them with a badge. */
export const Criteria: Story = {
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, criteria: "outside" }}>
      {(props) => <CriteriaFilter {...props} />}
    </WithFilters>
  ),
};

/** At least 50,000 a year. */
export const Pay: Story = {
  render: () => (
    <WithFilters
      initial={{ ...EMPTY_FILTERS, payMin: 50000, payPeriod: "year" }}
    >
      {(props) => <PayFilter {...props} />}
    </WithFilters>
  ),
};

/** What the newest application of each posting says. */
export const Application: Story = {
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, applications: ["waiting"] }}>
      {(props) => <ApplicationMenu {...props} />}
    </WithFilters>
  ),
};
