import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { AgentWait } from "@/components/shared/apply/agent-wait";
import {
  chatWith,
  CODE_ASK,
  SIGNIN_ASK,
  STEERED_ASK,
  WAIT_NOW,
} from "@/design-system/fixtures/agent-wait";
import { withApp } from "@/design-system/with-app";
import type {
  Application,
  ChatLine,
  Question,
} from "@/features/apply/api/apply-api";
import { fakeApplication } from "@/test/apply";
import { server } from "@/test/server";

const WAITS = fakeApplication("needs_person", {
  mode: "direct",
  needs: "agent_waits",
});

/** The detail the card reads: the application, the assistant's ask and the chat. */
function detail(
  application: Application,
  question: Question | null,
  chat: ChatLine[] = question ? chatWith(question) : [],
) {
  server.use(
    http.get("*/api/applications/:id", () =>
      HttpResponse.json({
        ...application,
        events: [],
        question,
        chat,
        now: WAIT_NOW,
      }),
    ),
  );
}

const meta = {
  title: "Shared/Apply/Agent wait",
  component: AgentWait,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
    withApp,
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The apply agent waits for the person (A25), on the application's card and in Your application: the conversation, with the assistant's sentence as its own words in plain text, what it asks for, the time it still waits, a box to answer (none for a code, typed in the browser), and the browser behind Watch the browser. Once the wait ended it says why; a wait with no ask to show says so.",
      },
    },
  },
  args: { application: WAITS },
} satisfies Meta<typeof AgentWait>;

export default meta;
type Story = StoryObj<typeof meta>;

/** It asks for a sign in: answer here, or use the browser. */
export const WaitsForASignIn: Story = {
  beforeEach: () => {
    detail(WAITS, SIGNIN_ASK);
  },
};

/** A code: typed in the browser, so no box. */
export const WaitsForACode: Story = {
  beforeEach: () => {
    detail(WAITS, CODE_ASK);
  },
};

/** The answer is in: the run reads it at its next look. */
export const AnswerSent: Story = {
  beforeEach: () => {
    detail(WAITS, SIGNIN_ASK, [
      ...chatWith(SIGNIN_ASK),
      {
        at: WAIT_NOW,
        who: "person",
        kind: "answer",
        text: "Create the account, please.",
        end: null,
      },
    ]);
  },
};

/** Asked after a send button: the site may have it already. */
export const AfterASendButton: Story = {
  args: { application: { ...WAITS, state: "submitting" } },
  beforeEach: () => {
    detail(
      { ...WAITS, state: "submitting" },
      { ...SIGNIN_ASK, after_send: true },
    );
  },
};

/** A page steered the assistant: markup and a link stay plain text. */
export const SteeredWords: Story = {
  beforeEach: () => {
    detail(WAITS, STEERED_ASK);
  },
};

/** Nobody came in time: the sentence stays, and why it stopped. */
export const StoppedWaiting: Story = {
  args: { application: { ...WAITS, needs: "agent_asked" } },
  beforeEach: () => {
    detail(
      { ...WAITS, needs: "agent_asked" },
      { ...SIGNIN_ASK, live: false },
      chatWith(SIGNIN_ASK, "person_late"),
    );
  },
};

/** The run waits, but its detail carries no ask: one sentence, and the page's Cancel. */
export const NoAskToShow: Story = {
  beforeEach: () => {
    detail(WAITS, null);
  },
};
