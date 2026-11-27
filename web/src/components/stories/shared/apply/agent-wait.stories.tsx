import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AgentWait } from "@/components/shared/apply/agent-wait";
import {
  AT_WORK_CHAT,
  chatWith,
  CODE_ASK,
  CODE_ASKED_TWICE,
  CODE_GIVEN,
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
import { agentWaitRoutes, agentWaitState } from "@/test/agent-wait";
import { fakeApplication } from "@/test/apply";
import { server } from "@/test/server";

const WAITS = fakeApplication("needs_person", {
  mode: "direct",
  needs: "agent_waits",
});

/**
 * The detail the card reads (the application, the assistant's ask and the chat), and the api's
 * answers to the boxes: an answer, a code, a message, each with the api's own sentences.
 */
function detail(
  application: Application,
  question: Question | null,
  chat: ChatLine[] = question ? chatWith(question) : [],
) {
  const read: Record<string, unknown> = { question, chat, now: WAIT_NOW };
  server.use(
    ...agentWaitRoutes(() => ({ application, detail: read }), agentWaitState()),
    http.get("*/api/applications/:id", () =>
      HttpResponse.json({ ...application, events: [], ...read }),
    ),
  );
}

const FILLING = fakeApplication("filling", { mode: "direct" });

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
          "The apply agent waits for the person (A25), on the application's card and in Your application: the conversation, with the assistant's sentence as its own words in plain text, what it asks for, the time it still waits, a box to answer (for a code, a code box the app types from, never shown to the assistant, and the browser's way beside it), and the browser behind Watch the browser. Once the wait ended it says why; a wait with no ask to show says so. While the assistant works, a box to write to it, each message in the chat with whether it was read.",
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

/** A code: the code box, typed on the site by the app; words beside it, and the browser's way. */
export const WaitsForACode: Story = {
  beforeEach: () => {
    detail(WAITS, CODE_ASK);
  },
};

/** A code of five characters: the api's own sentence leads to the browser, and the box is empty. */
export const ACodeRefused: Story = {
  beforeEach: () => {
    detail(WAITS, CODE_ASK);
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      await canvas.findByLabelText("The code the site sent you"),
      "48151",
    );
    await userEvent.click(
      canvas.getByRole("button", { name: "Give the code" }),
    );
    await canvas.findByRole("alert");
  },
};

/** The code is with the app: the api's line in the chat, never the code. */
export const ACodeGiven: Story = {
  beforeEach: () => {
    detail(WAITS, CODE_ASK, [...chatWith(CODE_ASK), CODE_GIVEN]);
  },
};

/** A code asked again after one was given: the site may not have taken it. */
export const ACodeAskedAgain: Story = {
  beforeEach: () => {
    detail(WAITS, CODE_ASK, CODE_ASKED_TWICE);
  },
};

/** The assistant fills the form: a box to write to it, one message read and one not read yet. */
export const WriteToTheAssistant: Story = {
  args: { application: FILLING, works: true },
  beforeEach: () => {
    detail(FILLING, null, AT_WORK_CHAT);
  },
};

/** A message that may hold a code while a code question waits: the api's own sentence. */
export const AMessageRefused: Story = {
  beforeEach: () => {
    detail(WAITS, CODE_ASK);
  },
  args: { works: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      await canvas.findByLabelText("Write to the assistant"),
      "Here it is: 7KD-3QX",
    );
    await userEvent.click(
      canvas.getByRole("button", { name: "Send the message" }),
    );
    await canvas.findByRole("alert");
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
