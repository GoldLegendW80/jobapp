import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { DecisionButtons } from "@/components/shared/apply/apply-section/decision-buttons";
import { withButtonRow } from "./button-row";

const meta = {
  title: "Shared/Apply/Apply section/Decision buttons",
  component: DecisionButtons,
  tags: ["autodocs"],
  decorators: [withButtonRow],
  parameters: {
    docs: {
      description: {
        component:
          "The decisions on an application that ask first, in Your application: It was submitted and Try again after a click the app could not confirm, Try again after the site refused it, and Cancel application while the api still takes it.",
      },
    },
  },
  args: {
    phase: "unknown",
    busy: false,
    questionAfterSend: false,
    retryKind: "retry",
    cancellable: false,
    onAsk: fn(),
  },
} satisfies Meta<typeof DecisionButtons>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The app could not confirm the send: the person says it was submitted, or tries again. */
export const CouldNotConfirm: Story = {
  name: "The send could not be confirmed",
};

/** The assistant asked a question after a possible send: its answer stands in for Try again. */
export const QuestionAfterTheSend: Story = {
  name: "A question stands in for Try again",
  args: { questionAfterSend: true },
};

/** The site refused the application: Try again. */
export const RefusedBySite: Story = {
  name: "Refused by the site",
  args: { phase: "refused" },
};

/** While the app works, Cancel application is offered. */
export const Cancellable: Story = {
  name: "Cancel while the app works",
  args: { phase: "filling", cancellable: true },
};

/** While a decision is on its way, every button waits. */
export const Busy: Story = {
  name: "Busy",
  args: { busy: true },
};

/** On a phone, the buttons stack and take the whole width. */
export const OnAPhone: Story = {
  name: "On a phone",
  parameters: { phone: true },
};
