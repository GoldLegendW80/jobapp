import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { JobsFrame } from "@/components/shared/jobs/jobs-frame";
import { StatusFilter } from "@/components/shared/jobs/filters/status-filter";
import { HeaderActions } from "@/components/shared/jobs/jobs-actions/header-actions";
import { Card, CardContent } from "@/components/ui/card";
import { STATUS_COUNTS } from "@/design-system/fixtures/job-filters";
import { WithFinder } from "@/design-system/fixtures/job-harnesses";
import { putOff } from "@/design-system/fixtures/onboarding";
import { withApp } from "@/design-system/with-app";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";
import { server } from "@/test/server";

/** The frame with the header's real actions, around a stand-in for the view and its statuses. */
function Frame({ view }: { view: string }) {
  return (
    <WithFinder>
      {(finder) => (
        <JobsFrame
          query="status=shortlisted"
          description="14 postings"
          actions={
            <HeaderActions finder={finder} selected={[]} empty={false} />
          }
        >
          <StatusFilter
            filters={{ ...EMPTY_FILTERS, statuses: ["shortlisted"] }}
            onChange={() => undefined}
            counts={STATUS_COUNTS}
            loading={false}
          />
          <Card>
            <CardContent className="py-24 text-center text-sm text-muted-foreground">
              {view}
            </CardContent>
          </Card>
        </JobsFrame>
      )}
    </WithFinder>
  );
}

const meta = {
  title: "Shared/Jobs/Jobs frame",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The Jobs area around both of its views: one header with the count and the actions, the List and Map tabs keeping the filters, and the setup reminder above the view.",
      },
      story: { inline: false, height: "560px" },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** On List: the tab is selected, the view below. */
export const List: Story = {
  name: "The list view",
  parameters: { at: "/jobs?status=shortlisted" },
  render: () => <Frame view="The list of postings and the one selected" />,
};

/** On Map: the same header, the Map tab selected, the filters kept. */
export const Map: Story = {
  name: "The map view",
  parameters: { at: "/jobs/map?status=shortlisted" },
  render: () => <Frame view="The map of postings" />,
};

/** A person who put the first setup off sees the reminder above the view. */
export const SetupReminder: Story = {
  name: "With the setup reminder",
  parameters: { at: "/jobs?status=shortlisted" },
  beforeEach: () => {
    server.use(
      http.get("*/api/opportunities/onboarding", () =>
        HttpResponse.json(putOff),
      ),
    );
  },
  render: () => <Frame view="The list of postings and the one selected" />,
};
