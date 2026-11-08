import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";

import { PostingLiveness } from "@/components/shared/jobs/posting/posting-liveness";
import {
  checkedPosting,
  CLOSED_CHECK,
  MAY_BE_CLOSED_CHECK,
  OPEN_CHECK,
  SIGN_IN_CHECK,
} from "@/design-system/fixtures/liveness";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Jobs/Posting liveness",
  component: PostingLiveness,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Is the posting still open: what the last check of its own address found, with the page's own sentence, in the posting's side rail. A closure the page states whole closes the posting by itself; closure words only propose it, and the person confirms with Mark closed. Check now asks the page again.",
      },
    },
  },
  decorators: [
    withApp,
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  args: { job: checkedPosting(MAY_BE_CLOSED_CHECK) },
  beforeEach: () => {
    server.use(
      http.post("*/api/jobs/:id/check", async () => {
        await delay(600);
        return HttpResponse.json(checkedPosting(OPEN_CHECK));
      }),
      http.patch("*/api/jobs/:id", () =>
        HttpResponse.json(
          checkedPosting(MAY_BE_CLOSED_CHECK, { status: "closed" }),
        ),
      ),
    );
  },
} satisfies Meta<typeof PostingLiveness>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Closure words on its page: the person decides with Mark closed, or asks again. */
export const MayBeClosed: Story = { name: "May be closed" };

/** Its page said, whole, that it is closed: the app moved it to Closed by itself. */
export const ClosedByItsPage: Story = {
  name: "Closed by its page",
  args: { job: checkedPosting(CLOSED_CHECK, { status: "closed" }) },
};

/** The last check read the page and found no sign of closing. */
export const NoSignOfClosing: Story = {
  name: "No sign of closing",
  args: { job: checkedPosting(OPEN_CHECK) },
};

/** The site asked to sign in: the check could not tell, and nothing moved. */
export const CouldNotRead: Story = {
  name: "Could not be read",
  args: { job: checkedPosting(SIGN_IN_CHECK) },
};

/** Never checked: the app reads its page before it writes documents or applies. */
export const NotCheckedYet: Story = {
  name: "Not checked yet",
  args: { job: checkedPosting(null) },
};

/** A LinkedIn posting: never asked, LinkedIn asks to sign in first. */
export const OnLinkedIn: Story = {
  name: "On LinkedIn",
  args: {
    job: checkedPosting(
      {
        verdict: "unconfirmed",
        reason: "linkedin",
        sentence: null,
        checked_at: "2026-11-07T09:30:00Z",
      },
      { url: "https://www.linkedin.com/jobs/view/4012345678/" },
    ),
  },
};
