import type { Meta, StoryObj } from "@storybook/react-vite";

import { LivenessBadge } from "@/components/shared/jobs/liveness-badge";
import {
  CLOSED_CHECK,
  MAY_BE_CLOSED_CHECK,
} from "@/design-system/fixtures/liveness";

const meta = {
  title: "Shared/Jobs/Liveness badge",
  component: LivenessBadge,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "May be closed: the last check of the posting's own address found that its page may say so, and the posting is still open. On a posting's header and on its Discover card; nothing otherwise.",
      },
    },
  },
  args: { liveness: MAY_BE_CLOSED_CHECK, status: "found" },
} satisfies Meta<typeof LivenessBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Closure words on the page of a posting still found. */
export const MayBeClosed: Story = { name: "May be closed" };

/** A page that said it is closed, on a posting the person opened again: still a proposal. */
export const ReopenedByThePerson: Story = {
  name: "Reopened by the person",
  args: { liveness: CLOSED_CHECK, status: "shortlisted" },
};

/** Once applied, a closed page changes nothing: no badge. */
export const AfterApplying: Story = {
  name: "After applying: nothing",
  args: { status: "applied" },
};
