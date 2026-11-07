import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { ApplySection } from "@/components/shared/apply/apply-section";
import {
  fakeFormShots,
  FILLED,
  inMinutes,
  ONE_LEFT,
} from "@/design-system/fixtures/applications";
import {
  AGENT_FILLED,
  APPLY_JOB,
  applicationIn,
  CHOSEN,
  DOCUMENTS,
  emailApproval,
  emailDraft,
  FACT_QUESTION,
  fakeEmailApplication,
  fakeQuestionAnswer,
  REFUSED_EVENTS,
  SUBMITTED_EVENTS,
} from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";
import type { Application } from "@/features/apply/api/apply-api";
import { fakeApply } from "@/test/apply";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Apply/Apply section",
  component: ApplySection,
  tags: ["autodocs"],
  decorators: [
    // The side rail of a posting's page, where the section opens; a phone is about as wide.
    (Story, { parameters }) => (
      <div className={parameters.wide ? "max-w-3xl" : "max-w-sm"}>
        <Story />
      </div>
    ),
    withApp,
  ],
  parameters: {
    docs: {
      story: { inline: false, height: "760px" },
      description: {
        component:
          "Your application, at the top of a posting's side rail: one Apply button, then the application followed live from the fill to the site's confirmation. Nothing is sent before the person's approval: the Apply click with the review box unticked, or Approve and submit after checking the filled form.",
      },
    },
  },
  args: { job: APPLY_JOB },
} satisfies Meta<typeof ApplySection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The application of the posting, its filled form and its screenshots. */
function apply(
  application: Application | null,
  options: { answers?: typeof FILLED; reviewBeforeSend?: boolean } = {},
) {
  fakeFormShots();
  const api = fakeApply({ application, ...options });
  api.documents = DOCUMENTS;
  return api;
}

const click =
  (name: string) =>
  async ({ canvasElement }: { canvasElement: HTMLElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", { name }),
    );
  };

/** Nothing started: Apply fills the form and sends it, the click being the approval; Apply myself opens the browser. */
export const ReadyToApply: Story = {
  name: "Ready to apply",
  beforeEach: () => {
    apply(null);
  },
};

/** With the review box ticked, Apply fills the form and waits for the person to check and send it. */
export const ReadyToApplyWithReview: Story = {
  name: "Ready to apply, review each form",
  beforeEach: () => {
    apply(null, { reviewBeforeSend: true });
  },
};

/** Apply clicked: the application starts and the page follows it. */
export const ClickApply: Story = {
  name: "Click Apply",
  beforeEach: () => {
    apply(null);
  },
  play: click("Apply"),
};

/** The apply settings could not be read: Apply waits, since what it does is the person's setting. */
export const SettingsNotRead: Story = {
  name: "Your settings could not be read",
  beforeEach: () => {
    apply(null);
    server.use(
      http.get("*/api/apply/settings", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** A LinkedIn posting before LinkedIn is allowed: how to allow it, and the posting's own link. */
export const LinkedInNotAllowed: Story = {
  name: "LinkedIn not allowed yet",
  args: {
    job: {
      ...APPLY_JOB,
      url: "https://www.linkedin.com/jobs/view/4100000001/",
    },
  },
  beforeEach: () => {
    apply(null);
    server.use(
      http.get("*/api/browser/linkedin", () =>
        HttpResponse.json({
          enabled: false,
          accepted: false,
          allowed: false,
          text: "LinkedIn may limit accounts that apply through tools.",
          text_version: 1,
          accepted_at: null,
          accepted_version: null,
          query: null,
          location: null,
          paused_until: null,
          last_sweep_at: null,
          needs_signin: false,
        }),
      ),
    );
  },
};

/** Waiting for the CV and letter, which wait for the documents consent: the link opens the CV tab. */
export const WaitingForTheDocuments: Story = {
  name: "Waiting for the documents consent",
  beforeEach: () => {
    apply(applicationIn("queued", { needs: "documents:consent" }));
  },
};

/** Waiting for a browser, with Cancel application. */
export const WaitingForABrowser: Story = {
  name: "Waiting for a browser",
  beforeEach: () => {
    apply(applicationIn("queued", { mode: "direct" }));
  },
};

/** One click Apply: the assistant fills the form and sends it by itself; the person can watch. */
export const FillingOneClick: Story = {
  name: "Filling, then it sends",
  beforeEach: () => {
    apply(applicationIn("filling", { mode: "direct" }));
  },
};

/** Filled and waiting: the answers, the documents, the form before the click, then Approve and submit or Reject. */
export const WaitingForApproval: Story = {
  name: "Waiting for your approval",
  beforeEach: () => {
    apply(applicationIn("ready"), { answers: FILLED });
  },
};

/** A required question is left: its row is open in amber and Approve waits for it. */
export const AQuestionLeft: Story = {
  name: "A question left for you",
  beforeEach: () => {
    apply(applicationIn("ready"), { answers: ONE_LEFT });
  },
};

/** One click Apply handed the form to the person, and the page says why. */
export const HandedBackToYou: Story = {
  name: "Handed back to you, and why",
  beforeEach: () => {
    apply(applicationIn("ready", { error_code: "agent_text" }), {
      answers: FILLED,
    });
  },
};

/** Approve and submit asks first, naming the company, the role and the documents. */
export const ApproveAsksFirst: Story = {
  name: "Approve and submit asks first",
  beforeEach: () => {
    apply(applicationIn("ready"), { answers: FILLED });
  },
  play: click("Approve and submit"),
};

/** Reject asks first too: nothing is sent. */
export const RejectAsksFirst: Story = {
  name: "Reject asks first",
  beforeEach: () => {
    apply(applicationIn("ready"), { answers: FILLED });
  },
  play: click("Reject"),
};

/** An application by e-mail: the message in full, and the recipient confirmed in the last dialog. */
export const ByEmail: Story = {
  name: "An application by e-mail",
  beforeEach: () => {
    apply(applicationIn("ready", { ats: "email" }));
    fakeEmailApplication(emailDraft());
    server.use(
      http.get("*/api/applications/:id/approval", () =>
        HttpResponse.json(emailApproval()),
      ),
    );
  },
};

/** Review first with the apply agent: every field it filled, then Confirm and send. */
export const CheckWhatTheAssistantFilled: Story = {
  name: "Check what the assistant filled",
  beforeEach: () => {
    const api = apply(applicationIn("ready"));
    Object.assign(api.detail, { review_agent: true, filled: AGENT_FILLED });
  },
};

/** A captcha needs the person: solve it in the live view, the assistant goes on. */
export const NeedsYouForACaptcha: Story = {
  name: "Needs you for a captcha",
  beforeEach: () => {
    apply(applicationIn("needs_person", { needs: "captcha" }), {
      answers: FILLED,
    });
  },
};

/** One click Apply's assistant asks a question it could not answer from the profile. */
export const TheAssistantAsks: Story = {
  name: "The assistant asks a question",
  beforeEach: () => {
    fakeQuestionAnswer();
    const api = apply(
      applicationIn("needs_person", {
        mode: "direct",
        needs: "agent_question",
      }),
      { answers: FILLED },
    );
    Object.assign(api.detail, { question: FACT_QUESTION });
  },
};

/** Full auto holds it so the person can cancel, then checks once more and submits. */
export const HeldByFullAuto: Story = {
  name: "Held by Full auto",
  beforeEach: () => {
    const api = apply(applicationIn("held", { mode: "full_auto" }), {
      answers: FILLED,
    });
    api.detail = { hold_until: inMinutes(10), now: new Date().toISOString() };
  },
};

/** Approved: the assistant fills the form again with exactly the approved answers, then submits. */
export const Submitting: Story = {
  name: "Approved, submitting",
  beforeEach: () => {
    apply(applicationIn("approved"), { answers: FILLED });
  },
};

/** Applied: what was sent, the page before and after the click, and the run report. */
export const Applied: Story = {
  name: "Applied",
  beforeEach: () => {
    const api = apply(
      applicationIn("submitted", {
        updated_at: "2026-11-05T08:07:13Z",
        submitted_at: "2026-11-05T08:07:13Z",
      }),
      { answers: FILLED },
    );
    api.events = SUBMITTED_EVENTS;
    api.screenshots = [
      { id: "shot-before", kind: "before", created_at: "2026-11-05T08:01:03Z" },
      { id: "shot-after", kind: "after", created_at: "2026-11-05T08:07:12Z" },
    ];
  },
};

/** One click Apply sent it, with the answers the assistant chose and why. */
export const SentByOneClick: Story = {
  name: "Sent by One click Apply",
  beforeEach: () => {
    const api = apply(
      applicationIn("submitted", {
        mode: "direct",
        updated_at: "2026-11-05T08:03:40Z",
        submitted_at: "2026-11-05T08:03:40Z",
      }),
      { answers: FILLED },
    );
    api.events = SUBMITTED_EVENTS;
    Object.assign(api.detail, { agent: true, chosen: CHOSEN });
  },
};

/** The app could not confirm it: the person looks, then says it was submitted or tries again. */
export const CouldNotConfirm: Story = {
  name: "Could not be confirmed",
  beforeEach: () => {
    const api = apply(applicationIn("submit_unknown"), { answers: FILLED });
    api.events = SUBMITTED_EVENTS.slice(0, 7);
  },
};

/** The site refused it after the click: check the screenshot, then try again. */
export const RefusedBySite: Story = {
  name: "Refused by the site",
  beforeEach: () => {
    const api = apply(applicationIn("refused_by_site"), { answers: FILLED });
    api.events = REFUSED_EVENTS;
    api.screenshots = [
      { id: "shot-before", kind: "before", created_at: "2026-11-05T08:01:03Z" },
      { id: "shot-after", kind: "after", created_at: "2026-11-05T08:07:12Z" },
    ];
  },
};

/** The last attempt failed: nothing was sent, and Apply is offered again. */
export const Failed: Story = {
  name: "The last attempt failed",
  beforeEach: () => {
    apply(applicationIn("failed", { error_code: "form_changed" }));
  },
};

/** The application is being read. */
export const Loading: Story = {
  name: "Loading",
  beforeEach: () => {
    apply(null);
    server.use(
      http.get("*/api/applications", async () => {
        await delay("infinite");
        return HttpResponse.json({ items: [] });
      }),
    );
  },
};

/** The application could not be read: Retry. */
export const LoadFailed: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    apply(null);
    server.use(
      http.get("*/api/applications", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** In a wide column, the buttons sit in a row and the screenshots side by side. */
export const Wide: Story = {
  name: "In a wide column",
  parameters: { wide: true },
  beforeEach: () => {
    const api = apply(applicationIn("refused_by_site"), { answers: FILLED });
    api.events = REFUSED_EVENTS;
    api.screenshots = [
      { id: "shot-before", kind: "before", created_at: "2026-11-05T08:01:03Z" },
      { id: "shot-after", kind: "after", created_at: "2026-11-05T08:07:12Z" },
    ];
  },
};
