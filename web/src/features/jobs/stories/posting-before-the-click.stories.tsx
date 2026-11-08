import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { AppAt } from "@/design-system/app-at";
import { postingDetail } from "@/design-system/fixtures/posting-details";
import { NOTICES } from "@/design-system/fixtures/posting-notices";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

const first = POSTING_DETAIL;

const meta = {
  title: "Pages/Jobs/Posting before the click",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The posting page, route /jobs/:id, when its text and the person's own rows have something to say before the click: under the header, what the posting says against the profile with its own words quoted, its closing date, a posting of another company with the same text and the person's applications to the company. Information only: Apply stays as it is.",
      },
    },
  },
  beforeEach: () => {
    fakeSession(member);
    server.use(
      http.get(`*/api/jobs/${first.id}`, () =>
        HttpResponse.json({ ...postingDetail(first), notices: NOTICES }),
      ),
    );
  },
  render: () => <AppAt path={`/jobs/${first.id}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to see before I click Apply what the posting says against my profile, when it closes, whether I already applied to the same role elsewhere and how the company answered me before, so that I decide with the facts in front of me. */
export const KnowBeforeIApply: Story = {};

/** As a job seeker on my phone, I want the same facts under the title, so that I read them before Your application. */
export const KnowBeforeIApplyOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
};
