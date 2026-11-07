import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { NeedsPerson } from "@/components/shared/apply/needs-person";
import {
  ACTION_QUESTION,
  APPLY_JOB,
  applicationIn,
  FACT_QUESTION,
  fakeQuestionAnswer,
} from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const needs = (reason: string, mode = "review") =>
  applicationIn("needs_person", { needs: reason, mode });

const WAITING_SINCE = new Date(Date.now() - 60_000).toISOString();

const meta = {
  title: "Shared/Apply/Needs person",
  component: NeedsPerson,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <div className="max-w-2xl">
        <Story />
      </div>
    ),
    withApp,
  ],
  parameters: {
    docs: {
      description: {
        component:
          "Needs you: what an application waits for the person for (a captcha, a code, a sign in, a question, a payment), with the live view for a step on the page, and the proposal to mark a closed posting closed. In Your application on a posting.",
      },
    },
  },
  args: { application: needs("captcha"), job: APPLY_JOB },
  beforeEach: () => {
    fakeQuestionAnswer();
    server.use(
      http.patch("*/api/jobs/:id", async () => {
        await delay(500);
        return HttpResponse.json({ ...APPLY_JOB, status: "closed" });
      }),
    );
  },
} satisfies Meta<typeof NeedsPerson>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A captcha: solve it in the live view, the assistant goes on by itself. Its events are still loading. */
export const ACaptcha: Story = {
  name: "A captcha, the live view waits",
};

/** The site sent a code by e-mail: the assistant waits for it on the page. */
export const AnEmailCode: Story = {
  name: "An e-mail code, the assistant waits",
  args: {
    application: needs("email_code"),
    events: [{ at: WAITING_SINCE, kind: "waiting_person", data: {} }],
  },
};

/** The person did not come in time: no live view, the application waits on the page. */
export const StoppedWaiting: Story = {
  name: "The assistant stopped waiting",
  args: {
    events: [
      { at: "2026-11-05T08:01:00Z", kind: "waiting_person", data: {} },
      { at: "2026-11-05T08:11:00Z", kind: "person_not_back", data: {} },
    ],
  },
};

/** The site refused the page: open it in the live view to see what it needs. */
export const SiteBlockedThePage: Story = {
  name: "The site blocked the page",
  args: { application: needs("forbidden"), events: [] },
};

/** Sign in or sign up on the site: Take over opens the browser on it. */
export const SignIn: Story = {
  name: "Sign in to the site",
  args: { application: needs("signin") },
};

/** A required question is left: answer it below, then approve. */
export const AQuestionLeft: Story = {
  name: "A question left for you",
  args: { application: needs("required_left") },
};

/** One click Apply's question on a fact: answer here and it goes on by itself. */
export const TheAssistantAsks: Story = {
  name: "The assistant asks a question",
  args: {
    application: needs("agent_question", "direct"),
    question: FACT_QUESTION,
  },
};

/** One click Apply's question on something only the person can do on the site. */
export const TheAssistantAsksForAnAction: Story = {
  name: "The assistant asks for a step on the site",
  args: {
    application: needs("agent_question", "direct"),
    question: ACTION_QUESTION,
  },
};

/** A payment was asked: nothing was typed or sent, and this may not be a genuine posting. */
export const APaymentWasAsked: Story = {
  name: "A payment was asked",
  args: { application: needs("payment_requested") },
};

/** LinkedIn showed a security check: all LinkedIn work waits seven days. */
export const LinkedInPaused: Story = {
  name: "LinkedIn is paused",
  args: { application: needs("linkedin_paused") },
};

/** A reason this build does not know: the form needs you, look at it below. */
export const AnotherReason: Story = {
  name: "Another reason",
  args: { application: needs("unexpected_widget") },
};

/** The site says the posting is closed: move it to Closed? */
export const PostingClosed: Story = {
  name: "The posting is closed",
  args: { application: applicationIn("posting_closed") },
};

/** Mark it closed: the button waits for the server. */
export const MarkItClosed: Story = {
  name: "Mark it closed",
  args: { application: applicationIn("posting_closed") },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Mark it closed",
      }),
    );
  },
};

/** Marking it closed fails: the error shows under the button, and it can be clicked again. */
export const MarkItClosedFails: Story = {
  name: "Mark it closed fails",
  args: { application: applicationIn("posting_closed") },
  beforeEach: () => {
    server.use(
      http.patch("*/api/jobs/:id", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
  play: MarkItClosed.play,
};

/** On a phone. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  args: {
    application: needs("agent_question", "direct"),
    question: FACT_QUESTION,
  },
};
