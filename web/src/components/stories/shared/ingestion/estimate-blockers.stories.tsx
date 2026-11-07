import type { Meta, StoryObj } from "@storybook/react-vite";

import { EstimateBlockers } from "@/components/shared/ingestion/run-start/estimate-blockers";
import { camilleEstimate } from "@/design-system/fixtures/camille-ingestion";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Ingestion/Estimate blockers",
  component: EstimateBlockers,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Why a run cannot start yet, as the estimate says, while the start button stays off: at the top of the Start panel.",
      },
    },
  },
  args: {
    estimate: {
      ...camilleEstimate(),
      can_start: false,
      blockers: [
        {
          code: "no_credential",
          reason: "Add a model credential before a run can start.",
        },
      ],
    },
  },
} satisfies Meta<typeof EstimateBlockers>;

export default meta;
type Story = StoryObj<typeof meta>;

/** No model credential: the link to the account page. */
export const NoCredential: Story = { name: "No model credential" };

/** Several reasons at once, each on its line. */
export const SeveralReasons: Story = {
  name: "Several reasons",
  args: {
    estimate: {
      ...camilleEstimate(),
      can_start: false,
      blockers: [
        {
          code: "no_credential",
          reason: "Add a model credential before a run can start.",
        },
        {
          code: "too_many_characters",
          reason:
            "The selected documents hold 1,820,000 characters, more than the 1,500,000 a run reads. Choose fewer documents.",
        },
        {
          code: "agent_off",
          reason:
            "The profile agent is turned off by the administrator for now.",
        },
      ],
    },
  },
};
