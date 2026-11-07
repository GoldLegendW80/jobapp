import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { EstimateView } from "@/components/shared/ingestion/run-start/estimate-view";
import { camilleEstimate } from "@/design-system/fixtures/camille-ingestion";
import type { Estimate } from "@/features/ingestion/api/runs-api";
import { fakeKeyEstimate } from "@/test/ingestion-runs";

const REUSED: Estimate = {
  ...camilleEstimate(),
  documents: camilleEstimate().documents.map((one, index) =>
    index === 0 ? { ...one, reused_steps: 1, images: 2 } : one,
  ),
  images: 2,
  reused_steps: 1,
};

const WITH_UNREADABLE: Estimate = {
  ...camilleEstimate(),
  unreadable: [
    {
      id: "doc-secret",
      name: "server-notes.txt",
      code: "secret",
      reason:
        "Its text may hold a password or a key. Check it under Documents first.",
    },
    {
      id: "doc-scan",
      name: "diploma-scan.pdf",
      code: "failed",
      reason: "No text could be read from this scan.",
    },
  ],
};

const meta = {
  title: "Shared/Ingestion/Estimate view",
  component: EstimateView,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The figures of one estimate as the server gave them: what is read, the tokens, the model, the cost on an API key and the limits of a run. Folded under the start button (Start panel).",
      },
    },
  },
  args: {
    estimate: camilleEstimate(),
    reread: [],
    refused: {},
    busy: false,
    onReread: () => undefined,
  },
} satisfies Meta<typeof EstimateView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** On a Claude subscription: tokens count against the quota, no amount. */
export const OnASubscription: Story = { name: "On a subscription" };

/** On an API key: the cost range and the cost of the pause, as estimates. */
export const OnAnApiKey: Story = {
  name: "On an API key, with the cost",
  args: { estimate: fakeKeyEstimate() },
};

function Reread() {
  const [reread, setReread] = useState<readonly string[]>([]);
  return (
    <EstimateView
      estimate={REUSED}
      reread={reread}
      refused={{}}
      busy={false}
      onReread={(id, on) => {
        setReread((old) =>
          on ? [...old, id] : old.filter((one) => one !== id),
        );
      }}
    />
  );
}

/** A document read before is reused; Read again uses tokens again. */
export const ReusedReadAgain: Story = {
  name: "Reused from an earlier run, read again",
  render: () => <Reread />,
};

/** Documents the run leaves out, each with the server's reason, and one refused at the start. */
export const SomeNotRead: Story = {
  name: "Some documents not read",
  args: {
    estimate: WITH_UNREADABLE,
    refused: {
      "doc-notes": "This document changed since the estimate. Choose it again.",
    },
  },
};

/** While a new estimate is asked, Read again waits. */
export const Busy: Story = {
  name: "While a new estimate is asked",
  args: { estimate: REUSED, reread: ["doc-cv"], busy: true },
};

/** On a phone the figures wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { estimate: fakeKeyEstimate() },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
