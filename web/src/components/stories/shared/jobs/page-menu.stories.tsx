import type { Meta, StoryObj } from "@storybook/react-vite";
import { screen, userEvent, within } from "storybook/test";

import { PageMenu } from "@/components/shared/jobs/jobs-actions/page-menu";
import { WithFinder } from "@/design-system/fixtures/job-harnesses";
import {
  fakeSchedule,
  WEEKDAYS_SCHEDULE,
} from "@/design-system/fixtures/jobs-actions";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";

/** The menu as the Jobs header holds it, with `selected` postings ticked. */
function Menu({ selected = [] }: { selected?: string[] }) {
  return (
    <div className="flex justify-end">
      <WithFinder>
        {(finder) => <PageMenu selected={selected} runner={finder.runner} />}
      </WithFinder>
    </div>
  );
}

const openMenu = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", { name: "More actions" }),
  );
};

const meta = {
  title: "Shared/Jobs/Page menu",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "More actions of the Jobs header: the automatic search with its state, past searches, and rescoring all postings or the ticked ones, each in its own dialog.",
      },
      story: { inline: false, height: "620px" },
    },
  },
  render: () => <Menu />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The icon button, at the end of the header's actions. */
export const Closed: Story = { name: "In the header" };

/** Open: the automatic search is off, nothing ticked to rescore. */
export const Open: Story = { name: "Open the menu", play: openMenu };

/** The automatic search on: the item says when it runs next. */
export const ScheduleOn: Story = {
  name: "The automatic search on",
  beforeEach: () => {
    fakeSchedule(WEEKDAYS_SCHEDULE);
  },
  play: openMenu,
};

/** Postings ticked: rescoring them is offered with their count. */
export const Selected: Story = {
  name: "Postings ticked",
  render: () => <Menu selected={POSTINGS.slice(0, 3).map((job) => job.id)} />,
  play: openMenu,
};

/** Automatic search opens its dialog. */
export const OpenTheSchedule: Story = {
  name: "Open the automatic search",
  play: async (context) => {
    await openMenu(context);
    await userEvent.click(
      await screen.findByRole("menuitem", { name: /Automatic search/ }),
    );
  },
};
