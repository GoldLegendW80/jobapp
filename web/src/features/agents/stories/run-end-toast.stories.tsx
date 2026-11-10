import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { toast } from "sonner";

import { Toaster } from "@/components/ui/sonner";
import { FAILED_RUN, SWEEP_RUN } from "@/design-system/fixtures/activity";
import { withApp } from "@/design-system/with-app";
import type { WorkflowRunDetail } from "@/features/agents/api/activity-api";
import { showRunEnd } from "@/features/agents/components/show-run-end";

const SEARCH: WorkflowRunDetail = {
  ...SWEEP_RUN,
  id: "story-search",
  status: "success",
};
const NOTHING_NEW: WorkflowRunDetail = {
  ...SWEEP_RUN,
  id: "story-search-empty",
  subject: { words: "Robotics engineer in Nantes", job_id: null },
  new_postings: 0,
};
const FAILED: WorkflowRunDetail = { ...FAILED_RUN, id: "story-failed" };
const CANCELLED: WorkflowRunDetail = {
  ...FAILED_RUN,
  id: "story-cancelled",
  workflow: "posting_documents",
  workflow_title: "Documents for a posting",
  subject: { words: "Data engineer at Example Freight", job_id: null },
  status: "cancelled",
  reason: null,
};
const RUNS = [SEARCH, NOTHING_NEW, FAILED, CANCELLED];

// Pinned in a Toaster of their own, so the Toaster of preview.tsx does not show them twice.
function PinnedToasts({ runs }: { runs: WorkflowRunDetail[] }) {
  useEffect(() => {
    for (const run of runs) {
      showRunEnd(run, { toasterId: "run-ends", duration: Infinity });
    }
    return () => {
      toast.dismiss();
    };
  }, [runs]);
  return (
    <Toaster
      id="run-ends"
      containerAriaLabel="Run end toasts pinned for review"
      expand
      visibleToasts={4}
    />
  );
}

const meta = {
  title: "Pages/Run end toast",
  decorators: [withApp],
  parameters: {
    layout: "fullscreen",
    docs: {
      description: {
        component:
          "On every page of the signed in app, a toast when one of the person's workflow runs ends: its workflow and status, what it was about, why it failed, how many postings a search added, and links to the run on Activity and to the new postings. The shell learns of the end from the Activity tab's count, read every 15 seconds while the page is visible. One toast per run: a run that ended before the page opened, or that another tab of this browser announced, shows none.",
      },
    },
  },
  render: () => <PinnedToasts runs={RUNS} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to hear when my search ends, how many postings it added and how my other runs ended, wherever I am in the app, so that I need not watch Activity. */
export const EveryEnd: Story = {};

/** As a job seeker on my phone, I want the same toasts, readable at my screen's width. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
};

/** As a job seeker who reads at night, I want the toasts in the dark theme. */
export const InTheDark: Story = {
  globals: { theme: "Dark" },
};
