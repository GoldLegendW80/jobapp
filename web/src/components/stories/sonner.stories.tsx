import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";

function wait(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

// Pinned in a Toaster of their own (toasterId), so the Toaster of preview.tsx does not show them twice.
function ReviewToasts() {
  useEffect(() => {
    const pinned = { toasterId: "review", duration: Infinity };
    toast.info("A run is already in progress. This page shows it.", {
      ...pinned,
      id: "info",
    });
    toast("Posting passed.", {
      ...pinned,
      id: "undo",
      action: { label: "Undo", onClick: () => undefined },
    });
    toast.error(
      "The CV could not be written. The run reached its token budget and was stopped. Raise the budget in Account, then try again.",
      { ...pinned, id: "error" },
    );
    toast.success("Application sent.", {
      ...pinned,
      id: "sent",
      description:
        "Senior data engineer, Lumen Freight. The site confirmed it.",
    });
    return () => {
      toast.dismiss();
    };
  }, []);
  return (
    <Toaster
      id="review"
      containerAriaLabel="Notifications pinned for review"
      expand
      visibleToasts={4}
    />
  );
}

const meta = {
  title: "Components/Feedback/Toast",
  component: Toaster,
  tags: ["autodocs"],
  parameters: {
    controls: { disable: true },
    docs: {
      description: {
        component:
          'Confirms an action the person just took, or says why it failed, without stopping them: "Application sent.", "Document deleted.". Reuse the verb of the button that started it. Never for something the person must act on: use Alert or AlertDialog. The app mounts one Toaster; call toast() from sonner.',
      },
    },
  },
} satisfies Meta<typeof Toaster>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Success after an action: the toast repeats the button's verb. */
export const Typical: Story = {
  render: () => (
    <Button
      onClick={() => {
        toast.success("Application sent.");
      }}
    >
      Send the application
    </Button>
  ),
};

/** In priority order: success for done, plain for neutral news, info, warning, error for a failure. */
export const Kinds: Story = {
  render: () => (
    <div className="flex flex-wrap gap-3">
      <Button
        variant="outline"
        onClick={() => {
          toast.success("Threshold saved.");
        }}
      >
        Success
      </Button>
      <Button
        variant="outline"
        onClick={() => {
          toast("Link copied.");
        }}
      >
        Plain
      </Button>
      <Button
        variant="outline"
        onClick={() => {
          toast.info("A run is already in progress. This page shows it.");
        }}
      >
        Info
      </Button>
      <Button
        variant="outline"
        onClick={() => {
          toast.warning("The reading paused at the limit you agreed to.");
        }}
      >
        Warning
      </Button>
      <Button
        variant="outline"
        onClick={() => {
          toast.error("Sign out failed. Server unreachable. Try again.");
        }}
      >
        Error
      </Button>
    </div>
  ),
};

/** Add a description for the one detail that tells this toast from the last one. */
export const WithDescription: Story = {
  render: () => (
    <Button
      onClick={() => {
        toast.success("Application sent.", {
          description:
            "Senior data engineer, Lumen Freight. The site confirmed it.",
        });
      }}
    >
      Send the application
    </Button>
  ),
};

/** Add an action to undo what just happened, for a choice that is easy to make by mistake. */
export const WithAction: Story = {
  render: () => (
    <Button
      variant="outline"
      onClick={() => {
        toast("Posting passed.", {
          action: {
            label: "Undo",
            onClick: () => {
              toast.success("Posting back in the deck.");
            },
          },
        });
      }}
    >
      Pass
    </Button>
  ),
};

/** A wait then a result in the same toast: loading names the action, success its outcome. */
export const Pending: Story = {
  render: () => (
    <Button
      onClick={() => {
        toast.promise(wait(2000), {
          loading: "Sending the application",
          success: "Application sent.",
          error: "The application could not be sent. Try again.",
        });
      }}
    >
      Send the application
    </Button>
  ),
};

/** A long error: what happened, then what to do. It wraps; keep it to two sentences. */
export const LongText: Story = {
  render: () => (
    <Button
      variant="outline"
      onClick={() => {
        toast.error(
          "The CV could not be written. The run reached its token budget and was stopped. Raise the budget in Account, then try again.",
        );
      }}
    >
      Write the CV
    </Button>
  ),
};

/** Open for review: success with a description, an error, an undo and an info, pinned. */
export const Shown: Story = {
  render: () => <ReviewToasts />,
  parameters: { docs: { story: { inline: false, height: "420px" } } },
};
