import type { Meta, StoryObj } from "@storybook/react-vite";
import { Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

// Shown in its own iframe on the docs page, so the open dialog does not cover the page.
const OPEN_FOR_REVIEW = {
  docs: { story: { inline: false, height: "420px" } },
} as const;

/** What one confirmation says: the question, what happens, and the verb of the confirm button. */
type Ask = {
  trigger: string;
  title: string;
  sentence: string;
  confirm: string;
  pending: string;
  done: string;
  destructive: boolean;
};

const DELETE_DOCUMENT: Ask = {
  trigger: "Delete document",
  title: "Delete this document?",
  sentence:
    "Cover letter for Analytics engineer, Atelier Brume. It is deleted for good; the posting and its CV stay.",
  confirm: "Delete document",
  pending: "Deleting",
  done: "Document deleted.",
  destructive: true,
};

const APPLY_TO_THREE: Ask = {
  trigger: "Apply to 3 postings",
  title: "Apply to 3 postings?",
  sentence:
    "The assistant fills and sends the form of each: Senior data engineer at Lumen Freight, Analytics engineer at Atelier Brume, Data platform lead at Kestrel Health. A sent application cannot be taken back.",
  confirm: "Apply to 3 postings",
  pending: "Applying",
  done: "Applying to 3 postings. Follow them in Applications.",
  destructive: false,
};

// The app's AlertDialog is controlled (no AlertDialogTrigger): the page opens it, the confirm closes it.
function Confirmation({
  ask,
  defaultOpen = false,
  pending = false,
}: {
  ask: Ask;
  defaultOpen?: boolean;
  pending?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const variant = ask.destructive ? "destructive" : "default";
  return (
    <>
      <Button
        variant={ask.destructive ? "outline" : "default"}
        onClick={() => {
          setOpen(true);
        }}
      >
        {ask.destructive ? <Trash2Icon /> : null}
        {ask.trigger}
      </Button>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{ask.title}</AlertDialogTitle>
            <AlertDialogDescription>{ask.sentence}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>Not yet</AlertDialogCancel>
            <Button
              variant={variant}
              disabled={pending}
              onClick={() => {
                setOpen(false);
                toast.success(ask.done);
              }}
            >
              {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
              {pending ? ask.pending : ask.confirm}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

const meta = {
  title: "Components/Overlays/Alert dialog",
  component: AlertDialog,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Stops the person before a destructive or irreversible action: delete a document, apply to several postings, cancel an application. The title asks the question, the description says what will happen, the confirm button repeats the verb. Not yet (or Cancel) is always there, and Escape sends nothing.",
      },
    },
  },
} satisfies Meta<typeof AlertDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Delete a document: the trigger and the confirm say the same thing. Click to open. */
export const Typical: Story = {
  render: () => <Confirmation ask={DELETE_DOCUMENT} />,
};

/** Open, for review: a destructive confirm, since a deleted document is gone for good. */
export const Open: Story = {
  render: () => <Confirmation ask={DELETE_DOCUMENT} defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** Irreversible but not destructive: the confirm is the primary button, and it names the count. */
export const ApplyToSeveral: Story = {
  render: () => <Confirmation ask={APPLY_TO_THREE} defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** While the action runs: both buttons disabled, the confirm says what it is doing. */
export const Pending: Story = {
  render: () => <Confirmation ask={DELETE_DOCUMENT} defaultOpen pending />,
  parameters: OPEN_FOR_REVIEW,
};

/** A long title and description wrap; on a phone the buttons stack, the confirm on top. */
export const LongText: Story = {
  render: () => (
    <Confirmation
      ask={{
        trigger: "Cancel application",
        title:
          "Cancel the application to Senior staff machine learning platform engineer at Kestrel Health?",
        sentence:
          "The form is filled and waits in the hold before it is sent. Cancelling stops it here: nothing is sent to Kestrel Health, and the posting goes back to your list as Shortlisted. You can apply again later from the posting page.",
        confirm: "Cancel application",
        pending: "Cancelling",
        done: "Application cancelled. Nothing was sent.",
        destructive: true,
      }}
      defaultOpen
    />
  ),
  parameters: OPEN_FOR_REVIEW,
};
