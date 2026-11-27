import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  application,
  fakeFormShots,
  FILLED,
  inMinutes,
  ONE_LEFT,
} from "@/design-system/fixtures/applications";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";
import {
  cold,
  fakeFollowUps,
  followUp,
  waiting,
  written,
} from "@/design-system/fixtures/follow-ups";
import {
  AT_WORK_CHAT,
  chatWith,
  CODE_ASK,
  SIGNIN_ASK,
  WAIT_NOW,
} from "@/design-system/fixtures/agent-wait";
import type { Question } from "@/features/apply/api/apply-api";
import { fakeQueue } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Applications/Applications",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The person's applications, of a Like or of an Apply click: what waits for their answer or approval first, then what is under way. Route /applications. Nothing is submitted without the person: Approve, a Like in Full auto or the Apply click is that approval.",
      },
    },
  },
  render: () => <AppAt path="/applications" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const ready = () =>
  application(1, "ready", 0, {
    shot_id: "shot-1",
    left: 0,
    required_left: 0,
  });
const writing = () =>
  application(2, "queued", 1, {
    needs: "documents:writing",
    documents: {},
  });

/** As a job seeker, I want to read the filled form and approve it in one click, so that it is sent with answers I checked. */
export const ApproveAReadyApplication: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeFormShots();
    const item = ready();
    fakeQueue([item, writing()], { [item.id]: FILLED });
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: "Review the answers" }),
    );
  },
};

/** As a job seeker, I want the question the app could not answer opened and named, so that I answer it before anything is sent. */
export const AnswerWhatTheFormLeft: Story = {
  beforeEach: () => {
    fakeSession(member);
    const item = application(1, "ready", 0, { left: 1, required_left: 1 });
    fakeQueue([item], { [item.id]: ONE_LEFT });
  },
};

/** As a job seeker, I want to approve every complete form at once, so that a morning of Likes takes one confirmation. */
export const ApproveAllReady: Story = {
  beforeEach: () => {
    fakeSession(member);
    const items = [
      application(1, "ready", 0),
      application(2, "ready", 1),
      application(3, "ready", 3),
    ];
    fakeQueue(
      items,
      Object.fromEntries(items.map((item) => [item.id, FILLED])),
    );
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: /Approve all ready/ }),
    );
  },
};

/** As a job seeker, I want to see what is being written, filled and sent, so that I know nothing is stuck. */
export const FollowApplicationsUnderWay: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([
      writing(),
      application(3, "filling", 2, { mode: "direct" }),
      application(4, "approved", 3),
    ]);
  },
};

/** As a job seeker, I want to stop every application under way in one click, told first which ones may already have been sent, so that nothing more goes out when I change my mind. */
export const CancelAllUnderWay: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([
      writing(),
      application(3, "filling", 2, { mode: "direct" }),
      application(4, "submitting", 3, { mode: "direct" }),
    ]);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: /Cancel all/ }),
    );
  },
};

/** As a job seeker, I want to stop an application the assistant is sending, told that the site may already have it, so that I know to check before I apply again. */
export const StopAfterASendButton: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([
      application(4, "submit_unknown", 3, {
        mode: "direct",
        error_code: "cancelled_by_person",
      }),
    ]);
  },
};

/** As a job seeker who clicked Apply, I want the card of an application the assistant sends by itself to list its documents without asking me to review them, so that my click stays the only approval it needs. */
export const DocumentsOfAnApplicationSentByItself: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([
      application(3, "filling", 2, {
        mode: "direct",
        documents: {
          cv: { number: 2, fit: "one_page", corrections: 0 },
          letter: { number: 2, fit: "one_page", corrections: 2 },
        },
      }),
    ]);
  },
};

/** As a job seeker, I want to watch the assistant fill and send my applications in my browser, so that I see what it does and can take over. */
export const WatchApplicationsInTheBrowser: Story = {
  beforeEach: () => {
    fakeSession(member);
    liveRoutes();
    const filling = application(3, "filling", 2, { mode: "direct" });
    browserHolds(
      lease({
        purpose: "apply_agent",
        application_id: filling.id,
        max_s: 3600,
      }),
    );
    fakeQueue([filling, application(4, "approved", 3)]);
    return fakeLiveScreens({ pages: ["form"] });
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: "Watch the browser" }),
    );
  },
};

/**
 * The apply agent waits for the person on `ask` (A25): its card, the detail with the chat, and its
 * lease waiting in the browser (`wait`: browserd's reason, `takeover` once the person has it).
 */
function assistantWaits(
  ask: Question,
  { wait = "ask_person", state = "needs_person" } = {},
) {
  fakeSession(member);
  liveRoutes();
  const item = application(3, state, 2, {
    mode: "direct",
    needs: "agent_waits",
  });
  browserHolds(
    lease({
      purpose: "apply_agent",
      application_id: item.id,
      state: "waiting_person",
      wait_reason: wait,
      wait_host: ask.host,
      wait_minutes_left: 11,
      max_s: 3600,
    }),
  );
  const queue = fakeQueue([item]);
  queue.now = WAIT_NOW;
  queue.details[item.id] = { question: ask, chat: chatWith(ask), agent: true };
  return fakeLiveScreens({ pages: ["form"] });
}

const watch = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", {
      name: "Watch the browser",
    }),
  );
};

/** As a job seeker, I want to read what the assistant asks me in its own words, answer it here or use the browser, so that the application goes on without starting again. */
export const AnswerTheAssistant: Story = {
  beforeEach: () => assistantWaits(SIGNIN_ASK),
};

/** As a job seeker, I want to give the code the site sent me in the app, so that the app types it on the site and the assistant never sees it. */
export const GiveACodeInTheApp: Story = {
  beforeEach: () => assistantWaits(CODE_ASK),
};

/** As a job seeker, I want to type a code the site sent me in the browser myself, so that the assistant goes on from the same page. */
export const TypeACodeInTheBrowser: Story = {
  beforeEach: () => assistantWaits(CODE_ASK),
  play: watch,
};

/** As a job seeker, I want to write to the assistant while it fills my application and see whether it read my message, so that it knows what I want without waiting for a question. */
export const WriteToTheAssistant: Story = {
  beforeEach: () => {
    fakeSession(member);
    const item = application(3, "filling", 2, { mode: "direct" });
    const queue = fakeQueue([item]);
    queue.now = WAIT_NOW;
    queue.details[item.id] = { chat: AT_WORK_CHAT, agent: true };
  },
};

/** As a job seeker, I want to know why the assistant stopped when a page showed my code again, so that I check the site, then finish in the browser or apply again. */
export const TheCodeShowedAgain: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([
      application(3, "submit_unknown", 2, {
        mode: "direct",
        error_code: "code_shown",
      }),
    ]);
  },
};

/** As a job seeker, I want to know that the browser is mine until I give it back and for how long, so that I finish what the site asks in time. */
export const UseTheBrowserDuringAWait: Story = {
  beforeEach: () => assistantWaits(SIGNIN_ASK, { wait: "takeover" }),
  play: watch,
};

/** As a job seeker, I want to be told when a send button was clicked before the assistant asked, so that I check the site before anything else. */
export const AnAskAfterASendButton: Story = {
  beforeEach: () =>
    assistantWaits(
      { ...SIGNIN_ASK, after_send: true },
      { state: "submitting" },
    ),
};

/** As a job seeker who did not come in time, I want to read what the assistant asked and try again when I am ready, so that nothing is lost. */
export const TheAssistantStoppedWaiting: Story = {
  beforeEach: () => {
    fakeSession(member);
    const item = application(3, "needs_person", 2, {
      mode: "direct",
      needs: "agent_asked",
    });
    const queue = fakeQueue([item]);
    queue.details[item.id] = {
      question: { ...SIGNIN_ASK, live: false },
      chat: chatWith(SIGNIN_ASK, "person_late"),
    };
  },
};

/** As a job seeker, I want the assistant's own account of a send it could not confirm, with It was not sent first when it says nothing was sent, so that I answer what really happened. */
export const WhatTheAssistantSaidOfTheSend: Story = {
  beforeEach: () => {
    fakeSession(member);
    const said = application(4, "submit_unknown", 2, {
      mode: "direct",
      error_code: "no_confirmation",
    });
    const queue = fakeQueue([
      application(1, "submit_unknown", 3, {
        mode: "direct",
        error_code: "send_clicked",
      }),
      application(2, "submit_unknown", 1, {
        mode: "direct",
        error_code: "person_late",
      }),
      said,
    ]);
    queue.details[said.id] = {
      events: [{ at: WAIT_NOW, kind: "agent_done", data: { sent: "no" } }],
    };
  },
};

/** As a job seeker, I want to say whether an application went through when the app could not tell, so that it is never sent twice. */
export const ConfirmWhatTheAppCouldNotSee: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([application(1, "submit_unknown", 2)]);
  },
};

/** As a job seeker, I want to know why the site refused a form and what I can do, so that I can fix it and try again. */
export const FixWhatTheSiteRefused: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeFormShots();
    fakeQueue([
      application(1, "refused_by_site", 3, {
        shot_id: "shot-1",
        reason: {
          code: "site_errors",
          sentence:
            "The site said the notice period must be a number of weeks, so nothing was sent.",
        },
      }),
    ]);
  },
};

/** As a job seeker in Full auto, I want to see what was sent today and cancel one still on hold, so that my Like stays my decision. */
export const CancelDuringTheFullAutoHold: Story = {
  beforeEach: () => {
    fakeSession(member);
    const api = fakeQueue([
      application(1, "held", 1, {
        mode: "full_auto",
        hold_until: inMinutes(12),
      }),
      application(2, "ready", 4, {
        mode: "review",
        reason: {
          code: "below_min_fit",
          sentence:
            "This posting's fit is below your threshold for Full auto, so it waits for you.",
        },
      }),
    ]);
    api.mode = "full_auto";
    api.now = new Date().toISOString();
    api.summary = {
      since: new Date().toISOString().slice(0, 10) + "T00:00:00Z",
      submitted: 3,
      held: 1,
      sent_to_review: [
        {
          code: "below_min_fit",
          sentence:
            "This posting's fit is below your threshold for Full auto, so it waits for you.",
          count: 1,
        },
      ],
    };
  },
};

/** As a job seeker with nothing in progress, I want the page to say where applications come from, so that I know what to do next. */
export const NothingInTheQueue: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([]);
  },
};

/** As a job seeker, I want to know when the queue could not be read, so that I try again instead of thinking it is empty. */
export const QueueUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    server.use(
      http.get("*/api/apply/queue", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
      http.get("*/api/apply/summary", () =>
        HttpResponse.json({
          since: "2026-11-06T00:00:00Z",
          submitted: 0,
          held: 0,
          sent_to_review: [],
        }),
      ),
    );
  },
};

/** As a job seeker on my phone, I want to approve and follow my applications, so that I can act between two things. */
export const ApplicationsOnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(member);
    fakeFormShots();
    const item = ready();
    fakeQueue([item, writing()], { [item.id]: FILLED });
  },
};

/** As a job seeker, I want to see which applications wait for a follow up and have a draft written on my click, so that I send it myself and the next date moves. */
export const FollowUpADueApplication: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([]);
    fakeFollowUps([followUp(), waiting(), cold()]);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    const [first] = await page.findAllByRole("button", {
      name: "Write a draft",
    });
    if (first) await userEvent.click(first);
  },
};

/** As a job seeker, I want the draft in my words before I send it from my own mail, then to say I sent it. */
export const SendAFollowUpFromMyMail: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([]);
    fakeFollowUps([written()]);
  },
};

/** As a job seeker, I want to turn follow ups off, so that the app reminds me of nothing. */
export const FollowUpsOff: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeQueue([]);
    fakeFollowUps([followUp()], false);
  },
};
