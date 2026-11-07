import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { NeedsYouDot } from "@/components/shared/apply/needs-person";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const needYou = (count: number) => () => {
  server.use(
    http.get("*/api/apply/queue/count", () =>
      HttpResponse.json({ waiting_on_you: count, needs_you: count }),
    ),
  );
};

const meta = {
  title: "Shared/Apply/Needs you dot",
  component: NeedsYouDot,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <p className="flex items-center text-sm font-medium">
        Applications
        <Story />
      </p>
    ),
    withApp,
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The red dot beside Applications in the app's navigation while at least one application needs the person; a screen reader hears how many.",
      },
    },
  },
} satisfies Meta<typeof NeedsYouDot>;

export default meta;
type Story = StoryObj<typeof meta>;

/** One application needs the person. */
export const OneNeedsYou: Story = {
  name: "One application needs you",
  beforeEach: needYou(1),
};

/** Several: still one dot, the count is read aloud. */
export const SeveralNeedYou: Story = {
  name: "Several need you",
  beforeEach: needYou(4),
};

/** Nothing needs the person: no dot. */
export const NothingNeedsYou: Story = {
  name: "Nothing needs you",
  beforeEach: needYou(0),
};
