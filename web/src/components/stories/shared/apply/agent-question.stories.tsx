import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AgentQuestion } from "@/components/shared/apply/agent-question";
import {
  ACTION_QUESTION,
  applicationIn,
  FACT_QUESTION,
  fakeQuestionAnswer,
  LONG_QUESTION,
} from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";

/** Something only the person can do, by the field's label as the page shows it. */
const actionOn = (label: string) => ({
  ...ACTION_QUESTION,
  label,
  sentence: `The assistant needs you on jobs.example.org for "${label}". Do it there, then go on.`,
});

const ASKED = applicationIn("needs_person", {
  mode: "direct",
  needs: "agent_question",
});

const meta = {
  title: "Shared/Apply/Agent question",
  component: AgentQuestion,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One click Apply's question: the assistant stopped on a field it could not fill while nobody watched. A fact gets a text box and Remember this answer; something only the person can do on the site gets the live view, named after the step, then I did it, go on, the one primary action. In Your application, under Needs you.",
      },
    },
  },
  args: { application: ASKED, question: FACT_QUESTION },
  decorators: [
    (Story) => (
      <div className="max-w-xl">
        <Story />
      </div>
    ),
    withApp,
  ],
  beforeEach: () => {
    fakeQuestionAnswer();
  },
} satisfies Meta<typeof AgentQuestion>;

export default meta;
type Story = StoryObj<typeof meta>;

const typeAnswer = async ({
  canvasElement,
}: {
  canvasElement: HTMLElement;
}) => {
  const canvas = within(canvasElement);
  await userEvent.type(
    await canvas.findByLabelText("Your answer"),
    "One month, from the day I sign.",
  );
};

const sendAnswer = async (context: { canvasElement: HTMLElement }) => {
  await typeAnswer(context);
  const canvas = within(context.canvasElement);
  await userEvent.click(canvas.getByLabelText("Remember this answer"));
  await userEvent.click(
    canvas.getByRole("button", { name: "Send the answer" }),
  );
};

/** A fact the profile does not hold: the box waits for an answer before Send. */
export const AFactToAnswer: Story = {
  name: "A fact to answer",
};

/** Answered, and kept for next forms: Send the answer can be clicked. */
export const Answered: Story = {
  name: "Answered, remembered for next forms",
  play: async (context) => {
    await typeAnswer(context);
    await userEvent.click(
      within(context.canvasElement).getByLabelText("Remember this answer"),
    );
  },
};

/** While the answer goes to the assistant, the button waits. */
export const Sending: Story = {
  name: "Sending the answer",
  beforeEach: () => {
    fakeQuestionAnswer(async () => {
      await delay("infinite");
      return HttpResponse.json({});
    });
  },
  play: sendAnswer,
};

/** The answer did not go: the server's sentence shows under the button. */
export const SendFails: Story = {
  name: "The answer could not be sent",
  beforeEach: () => {
    fakeQuestionAnswer(() =>
      HttpResponse.json(
        { detail: "This question was already answered in another tab." },
        { status: 409 },
      ),
    );
  },
  play: sendAnswer,
};

/** A check on the site: the live view opens it, then I did it, go on, the one primary action. */
export const SomethingToDoOnTheSite: Story = {
  name: "Something to do on the site",
  args: { question: ACTION_QUESTION },
};

/** A code the site sent: the live view opens to enter it. */
export const ACodeToEnter: Story = {
  name: "A code to enter on the site",
  args: {
    question: actionOn("Verification code"),
  },
};

/** A password the assistant never types: the live view opens to sign in. */
export const ASignIn: Story = {
  name: "A sign in on the site",
  args: {
    question: actionOn("Password"),
  },
};

/** A step its label does not name: the live view opens, with no promise of what is there. */
export const AnotherStep: Story = {
  name: "Another step on the site",
  args: {
    question: actionOn("Upload a copy of your ID"),
  },
};

/** Asked after a control that may have sent the form: the page says so first, and the answer is a Try again. */
export const AfterAPossibleSend: Story = {
  name: "After a possible send, a fact",
  args: {
    application: applicationIn("submit_unknown", { mode: "direct" }),
    question: { ...FACT_QUESTION, after_send: true },
  },
};

/** The same for something to do on the site. */
export const AfterAPossibleSendOnTheSite: Story = {
  name: "After a possible send, on the site",
  args: {
    application: applicationIn("submit_unknown", { mode: "direct" }),
    question: { ...ACTION_QUESTION, after_send: true },
  },
};

/** A long question wraps. */
export const LongQuestion: Story = {
  name: "Long question",
  args: { question: LONG_QUESTION },
};

/** On a phone, the button takes the whole width. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
