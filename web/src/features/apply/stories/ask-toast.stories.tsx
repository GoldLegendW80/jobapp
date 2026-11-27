import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { toast } from "sonner";

import { Toaster } from "@/components/ui/sonner";
import { withApp } from "@/design-system/with-app";
import type { AskToast } from "@/features/apply/api/use-ask-toasts";
import { showAsk } from "@/features/apply/components/show-ask";

const ASKS: AskToast[] = [
  {
    key: "story-ask-1",
    title: "Robot Arm Engineer",
    company: "Example Robotics",
    postingId: "00000000-0000-4000-9000-000000000001",
  },
  {
    key: "story-ask-2",
    title:
      "Senior Embedded Software Engineer, Motion Control and Safety Systems for Autonomous Mobile Robots",
    company: "Example Freight Logistics International",
    postingId: "00000000-0000-4000-9000-000000000002",
  },
];

// Pinned in a Toaster of their own, so the Toaster of preview.tsx does not show them twice.
function PinnedToasts({ asks }: { asks: AskToast[] }) {
  useEffect(() => {
    for (const ask of asks) {
      showAsk(ask, { toasterId: "asks", duration: Infinity });
    }
    return () => {
      toast.dismiss();
    };
  }, [asks]);
  return (
    <Toaster
      id="asks"
      containerAriaLabel="Ask toasts pinned for review"
      expand
      visibleToasts={4}
    />
  );
}

const meta = {
  title: "Pages/The assistant needs you toast",
  decorators: [withApp],
  parameters: {
    layout: "fullscreen",
    docs: {
      description: {
        component:
          "On every page of the signed in app, a toast when the apply agent asks for the person (A25): the posting and its company, and a link to the application on the posting's page, where the person answers or uses the browser. The shell learns of it from its count of the applications that need the person; when that count rises it reads the queue and each waiting application once. One toast per ask: an ask that ended, or that another tab of this browser announced, shows none. The assistant's own words never go into the toast.",
      },
    },
  },
  render: () => <PinnedToasts asks={ASKS} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to hear when the assistant needs me for an application, wherever I am in the app, so that it does not wait for nothing. */
export const AsksForYou: Story = {};

/** As a job seeker on my phone, I want the same toast, readable at my screen's width, with a long posting title. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
};

/** As a job seeker who reads at night, I want the toast in the dark theme. */
export const InTheDark: Story = {
  globals: { theme: "Dark" },
};
