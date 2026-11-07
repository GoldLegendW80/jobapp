import type { Meta, StoryObj } from "@storybook/react-vite";

import { Metric, RunMetrics } from "@/components/shared/agents/run/run-metrics";
import { makeRun } from "@/design-system/fixtures/runs";

const FINISHED_AT = new Date("2026-11-05T08:14:21Z").getTime();

const meta = {
  title: "Shared/Agents/Run metrics",
  component: RunMetrics,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A run's model, tokens, amount and duration, and what the amount is when it is not a bill: in the run panel and on the page of a run.",
      },
    },
  },
  args: { run: makeRun(), now: FINISHED_AT },
} satisfies Meta<typeof RunMetrics>;

export default meta;
type Story = StoryObj<typeof meta>;

/** On a Claude subscription the amount is an estimate at API prices, and the note under it says so. */
export const OnASubscription: Story = {
  name: "Finished on a subscription",
};

/** With an Anthropic API key the amount is what the run cost: no note. */
export const WithAnApiKey: Story = {
  name: "Finished with an API key",
  args: { run: makeRun({ credential_kind: "anthropic_api_key" }) },
};

/** A run whose credential was not recorded: the amount at API prices, without saying it was billed. */
export const CredentialNotRecorded: Story = {
  name: "Credential not recorded",
  args: { run: makeRun({ credential_kind: null }) },
};

/** While the run works: no total yet, the duration counts from its start. */
export const AtWork: Story = {
  name: "Run at work, no total yet",
  args: {
    run: makeRun({
      status: "running",
      model: null,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
      started_at: new Date(Date.now() - 42_000).toISOString(),
      finished_at: null,
      duration_s: null,
    }),
    now: Date.now(),
  },
};

/** A very small amount reads "Under $0.01", never "$0.00". */
export const UnderACent: Story = {
  name: "Amount under a cent",
  args: {
    run: makeRun({
      input_tokens: 412,
      output_tokens: 18,
      cost_usd: 0.0021,
      credential_kind: "anthropic_api_key",
    }),
  },
};

/** A page adds its own facts around them: the agent first, the posting last. */
export const WithFactsOfThePage: Story = {
  name: "With facts of the page around them",
  args: {
    before: <Metric label="Agent" value="Posting check" />,
    after: (
      <Metric label="Posting" value="Analytics engineer at Kestrel Health" />
    ),
    className: "sm:grid-cols-3",
  },
};

/** On a phone the facts stack two by two. */
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
