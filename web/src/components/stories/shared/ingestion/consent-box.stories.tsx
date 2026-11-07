import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { ConsentBox } from "@/components/shared/ingestion/run-start/consent-box";
import {
  CONSENT_ASKED,
  CONSENT_LONG,
} from "@/design-system/fixtures/shared-ingestion";

const meta = {
  title: "Shared/Ingestion/Consent box",
  component: ConsentBox,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What is sent, and to whom, with the box that agrees to that version of the text: above the start button of a person's first run (Start panel). Until it is ticked the run cannot start.",
      },
    },
  },
  args: {
    consent: CONSENT_ASKED,
    ticked: false,
    changed: false,
    disabled: false,
    onTick: () => undefined,
  },
} satisfies Meta<typeof ConsentBox>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Not agreed yet: the text, and the box unticked. */
export const NotTicked: Story = { name: "Not ticked yet" };

/** Agreed to this version of the text. */
export const Ticked: Story = { name: "Ticked", args: { ticked: true } };

/** The text changed after the tick: the tick no longer counts, read it again. */
export const TextChangedAfterTheTick: Story = {
  name: "The text changed after the tick",
  args: { consent: CONSENT_LONG, changed: true },
};

/** While the run starts the box cannot change. */
export const WhileTheRunStarts: Story = {
  name: "While the run starts",
  args: { ticked: true, disabled: true },
};

function TickableBox() {
  const [version, setVersion] = useState<number | null>(null);
  return (
    <ConsentBox
      consent={CONSENT_LONG}
      ticked={version === CONSENT_LONG.version}
      changed={false}
      disabled={false}
      onTick={setVersion}
    />
  );
}

/** A longer text, and the box that ticks and unticks. */
export const TickTheBox: Story = {
  name: "Tick the box, long text",
  render: () => <TickableBox />,
};

/** On a phone the text and the label wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { consent: CONSENT_LONG },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
