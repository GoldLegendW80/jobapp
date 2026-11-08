import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  FEW_INSIGHTS,
  INSIGHTS,
  NO_INSIGHTS,
} from "@/design-system/fixtures/insights";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import { fakeInsights } from "@/test/insights";
import { fakeJobsApi } from "@/test/jobs";

const meta = {
  title: "Pages/Jobs/Your search in numbers",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The person's own counts, reached from the Jobs page's More actions menu: the funnel from found to interview, interviews by fit, by application site and by source, the days to a first answer, the reasons given to Passes, and the silent applications with their reviewed close. No model and no advice; a group under its floor says Too few to say. Route /jobs/insights.",
      },
    },
  },
  render: () => <AppAt path="/jobs/insights" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const signedIn = () => {
  fakeSession(member);
  fakeJobsApi(POSTINGS);
};

/** As a job seeker, I want to see how my applications turn into answers and interviews, so that I know where my search stands. */
export const SeeWhereMySearchStands: Story = {
  beforeEach: () => {
    signedIn();
    fakeInsights(INSIGHTS);
  },
};

/** As a job seeker just starting, I want the page to say when there are too few applications to tell, so that I read no pattern into a handful. */
export const TooFewToSay: Story = {
  beforeEach: () => {
    signedIn();
    fakeInsights(FEW_INSIGHTS);
  },
};

/** As a job seeker, I want to close the applications nobody answered in one reviewed click, so that my list shows what is still open. */
export const CloseTheSilentApplications: Story = {
  beforeEach: () => {
    signedIn();
    fakeInsights(INSIGHTS);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("checkbox", {
        name: "Machine Learning Engineer at Example Labs",
      }),
    );
    await userEvent.click(
      await page.findByRole("button", { name: "Close 1 application" }),
    );
  },
};

/** As a new job seeker, I want to know why the page is empty, so that I go find postings first. */
export const NothingToCountYet: Story = {
  beforeEach: () => {
    signedIn();
    fakeInsights(NO_INSIGHTS);
  },
};

/** As a job seeker on my phone, I want the numbers to read in one column, so that I can check them anywhere. */
export const NumbersOnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    signedIn();
    fakeInsights(INSIGHTS);
  },
};
