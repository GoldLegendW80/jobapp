import type { Meta, StoryObj } from "@storybook/react-vite";

import { COLUMN } from "@/components/shared/jobs/setup-grid";
import {
  BODY,
  SetupSurface,
} from "@/components/shared/jobs/search-setup/setup-surface";
import { phoneScreen } from "@/design-system/fixtures/phone-screen";
import { cn } from "@/lib/utils";

const meta = {
  title: "Shared/Jobs/Setup surface",
  component: SetupSurface,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Where Find jobs opens: a shadcn Dialog in the middle of a wide screen, a shadcn Drawer from the bottom on a phone (dragged down to close), its header and footer in view and its middle scrolling.",
      },
      story: { inline: false, height: "520px" },
    },
  },
  args: {
    onClose: () => undefined,
    onReturnFocus: () => undefined,
    children: (
      <div className={cn(BODY, COLUMN)}>
        <p className="max-w-prose text-sm">
          The two steps of Find jobs go here: where to search, then what you
          want.
        </p>
      </div>
    ),
  },
} satisfies Meta<typeof SetupSurface>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A wide screen: the dialog, up to 42rem wide. */
export const Dialog: Story = { name: "A dialog on a wide screen" };

/**
 * A phone: the drawer from the bottom, with its grab handle. The story answers the phone query of
 * `window.matchMedia`, which `useIsPhone` reads, and makes the page 390 px wide (`phoneScreen`).
 */
export const Drawer: Story = {
  name: "A drawer from the bottom on a phone",
  beforeEach: phoneScreen,
};
