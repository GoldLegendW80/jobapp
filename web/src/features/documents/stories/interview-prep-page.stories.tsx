import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import { camille } from "@/design-system/fixtures/camille-profile";
import {
  NOT_PREPARED,
  fakeInterviewPrep,
  preparedFor,
} from "@/design-system/fixtures/interview-prep";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeSession } from "@/test/auth";

const JOB = { ...POSTING_DETAIL, status: "interview" as const };

const meta = {
  title: "Pages/Documents/Interview preparation",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The interview preparation of one posting: the questions to expect (inferred from the posting and the hiring panel, never reported), the gaps and how to answer them, STAR stories from the profile, questions to ask and the Debrief in the main column; Practice in the side rail. Route /jobs/:id/interview, opened from the posting page.",
      },
    },
  },
  render: () => <AppAt path={`/jobs/${JOB.id}/interview`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a person invited to an interview, I want to prepare it in one click, so that I know what to expect. */
export const PrepareInOneClick: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeInterviewPrep(JOB, NOT_PREPARED, { consent: { accepted: true } });
  },
};

/** As a person invited to an interview, I want the questions the posting implies and my stories with my own figures, so that I rehearse what matters. */
export const ReadThePreparation: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeInterviewPrep(JOB, preparedFor());
  },
};

/** As a person, I want to rehearse one question at a time with feedback, so that my answers get sharper. */
export const Practice: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeInterviewPrep(
      JOB,
      preparedFor(
        {},
        {
          practice: [
            {
              id: 1,
              role: "user",
              text: "Ask me the first question.",
              at: "2026-11-07T09:05:00Z",
            },
            {
              id: 2,
              role: "assistant",
              text: "Recruiter round: why Lumen Freight, and why now?",
              at: "2026-11-07T09:05:20Z",
            },
            {
              id: 3,
              role: "user",
              text: "I want to run pipelines whose results people use every morning.",
              at: "2026-11-07T09:06:00Z",
            },
          ],
          practice_open: true,
        },
      ),
    );
  },
};

/** As a person, I want to keep the stories that worked in my profile, so that the next preparation reuses them, and nothing changes until I accept. */
export const KeepStories: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeInterviewPrep(
      JOB,
      preparedFor({}, { stories_proposal: "stories-proposal" }),
    );
  },
};

/** As a person after an interview, I want to write down what was asked, so that the next round starts from it. */
export const Debrief: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeInterviewPrep(
      JOB,
      preparedFor({
        debrief: [
          {
            on: "First call with the hiring manager",
            asked: "How I would rebuild the pricing pipeline from scratch.",
            notes: "Too long on the history, too short on the result.",
          },
        ],
      }),
    );
  },
};
