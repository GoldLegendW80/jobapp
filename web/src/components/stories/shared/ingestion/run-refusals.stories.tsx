import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunRefusals } from "@/components/shared/ingestion/run-refusals";
import { CAMILLE_REFUSED } from "@/design-system/fixtures/camille-ingestion";

const meta = {
  title: "Shared/Ingestion/Run refusals",
  component: RunRefusals,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Why proposals of a finished run were not kept: one quiet line per section and rule, in the person's words. Under the figures of Run progress and at the head of the review of Fill my profile; nothing when the checks kept everything.",
      },
    },
  },
  args: { refused: CAMILLE_REFUSED },
} satisfies Meta<typeof RunRefusals>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A CV read in two columns: every skill had a field skills do not have, one date two digits. */
export const SomeNotKept: Story = { name: "Some proposals not kept" };

/** One proposal, one reason: the line says "was". */
export const OneNotKept: Story = {
  name: "One proposal not kept",
  args: {
    refused: [
      {
        section: "facts",
        rule: "excerpt_not_found",
        count: 1,
        sentence: "excerpt not found",
      },
    ],
  },
};

/** A rule this page does not know yet still reads as a plain sentence. */
export const UnknownRule: Story = {
  name: "A rule the page does not know",
  args: {
    refused: [
      {
        section: "answers",
        rule: "a_rule_of_a_newer_server",
        count: 3,
        sentence: "a sentence for the assistant",
      },
    ],
  },
};

/** Everything was kept: nothing shows. */
export const AllKept: Story = { name: "All kept", args: { refused: [] } };

/** On a phone each line wraps under the next. */
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
