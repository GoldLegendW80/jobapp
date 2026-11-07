import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { FinishSetupCard } from "@/components/shared/onboarding/onboarding-gate";
import { putOff } from "@/design-system/fixtures/onboarding";
import { withApp } from "@/design-system/with-app";
import { onboardingDone } from "@/test/handlers";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Onboarding/Finish setup card",
  component: FinishSetupCard,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Finish setting up, on the Jobs page while the first login wizard was put off and steps are left: one line on why, and Continue setup. Hidden once the wizard is done.",
      },
    },
  },
  beforeEach: () => {
    server.use(
      http.get("*/api/opportunities/onboarding", () =>
        HttpResponse.json(putOff),
      ),
    );
  },
} satisfies Meta<typeof FinishSetupCard>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The wizard was put off with Skip for now. */
export const StepsLeft: Story = { name: "Steps left" };

/** On a phone the button goes under the text. */
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

/** The wizard is done: the card is not shown. */
export const Done: Story = {
  name: "Wizard done, nothing shown",
  beforeEach: () => {
    server.use(
      http.get("*/api/opportunities/onboarding", () =>
        HttpResponse.json(onboardingDone),
      ),
    );
  },
};
