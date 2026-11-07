import type { Meta, StoryObj } from "@storybook/react-vite";
import { useId } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";

// Shown in its own iframe on the docs page, so the open dialog does not cover the page.
const OPEN_FOR_REVIEW = {
  docs: { story: { inline: false, height: "420px" } },
} as const;

const POSTING = [
  "Lumen Freight moves goods for 300 shops across France from 40 depots. Every truck, pallet and delivery leaves a trace, and the data team turns those traces into the plans the depots work from each morning.",
  "As a senior data engineer, you own the pipelines that bring depot data into the warehouse: Airflow for orchestration, dbt for the models, Python where SQL is not enough. You decide how they are tested and monitored.",
  "You work with two analysts and a product manager. You review their models, help them write faster queries, and say no to a dashboard when a table would do.",
  "What we ask: five years of data engineering, Python and SQL you are proud of, and pipelines you have kept running for more than a year. French or English, both are spoken in the team.",
  "What we offer: 55,000 to 65,000 EUR a year, two days a week at home, a train pass paid in full, and a week each year to learn what you choose.",
];

function AddPostingDialog({
  defaultOpen = false,
  state = "idle",
}: {
  defaultOpen?: boolean;
  state?: "idle" | "error" | "pending";
}) {
  const id = useId();
  const pending = state === "pending";
  return (
    <Dialog defaultOpen={defaultOpen}>
      <DialogTrigger asChild>
        <Button>Add a posting by hand</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a posting by hand</DialogTitle>
          <DialogDescription>
            Paste the link of a posting you found yourself. The app reads it and
            scores it against your profile.
          </DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field data-invalid={state === "error" || undefined}>
            <FieldLabel htmlFor={`${id}-link`}>Link to the posting</FieldLabel>
            <Input
              id={`${id}-link`}
              type="url"
              placeholder="https://"
              defaultValue={
                state === "idle"
                  ? undefined
                  : "https://lumen-freight.example.org/jobs"
              }
              aria-invalid={state === "error" || undefined}
              disabled={pending}
            />
            {state === "error" ? (
              <FieldError>
                This link opens a list of jobs, not one posting. Open the
                posting itself and copy its link.
              </FieldError>
            ) : (
              <FieldDescription>
                From any job site or a company&apos;s own page.
              </FieldDescription>
            )}
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-note`}>Note (optional)</FieldLabel>
            <Textarea
              id={`${id}-note`}
              placeholder="A friend works there"
              disabled={pending}
            />
          </Field>
        </FieldGroup>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={pending}>
              Cancel
            </Button>
          </DialogClose>
          <Button
            disabled={pending}
            onClick={() => {
              toast.success("Posting added.");
            }}
          >
            {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
            {pending ? "Adding" : "Add posting"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const meta = {
  title: "Components/Overlays/Dialog",
  component: Dialog,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A short task that needs the person's input before they go on: add a posting by hand, rename a document, answer a question of an application form. It blocks the page, so keep it to one task and one primary action. Always a DialogTitle, sr-only if the content names itself.",
      },
    },
  },
} satisfies Meta<typeof Dialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The trigger names what opens; the dialog repeats it as its title. Click to open. */
export const Typical: Story = {
  render: () => <AddPostingDialog />,
};

/** Open, for review: header, fields, then Cancel and the primary action. */
export const Open: Story = {
  render: () => <AddPostingDialog defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** An error on a field: what is wrong and how to fix it, under the field, not in a toast. */
export const WithError: Story = {
  render: () => <AddPostingDialog defaultOpen state="error" />,
  parameters: OPEN_FOR_REVIEW,
};

/** While the action runs: both buttons disabled, the primary one says what it is doing. */
export const Pending: Story = {
  render: () => <AddPostingDialog defaultOpen state="pending" />,
  parameters: OPEN_FOR_REVIEW,
};

/** Information only: no form, one way out. Use it for an explanation too long for a tooltip. */
export const Explanation: Story = {
  render: () => (
    <Dialog defaultOpen>
      <DialogTrigger asChild>
        <Button variant="link">What is the fit score?</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>The fit score</DialogTitle>
          <DialogDescription>
            A number from 0 to 100: how closely a posting matches your profile.
          </DialogDescription>
        </DialogHeader>
        <p className="text-sm">
          It counts the skills the posting asks for that you have, then takes
          points off for a requirement you do not meet, such as a language or a
          number of years. Your thresholds decide what the app does with it.
        </p>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Close</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** Long content scrolls inside the dialog; the title and the close button stay reachable. */
export const LongContent: Story = {
  render: () => (
    <Dialog defaultOpen>
      <DialogTrigger asChild>
        <Button variant="outline">Read the posting</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Senior data engineer</DialogTitle>
          <DialogDescription>Lumen Freight, Lyon, Hybrid</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3 text-sm">
          {POSTING.map((paragraph) => (
            <p key={paragraph.slice(0, 24)}>{paragraph}</p>
          ))}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Close</Button>
          </DialogClose>
          <Button>Apply</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** The content names itself (a page of a CV): the title is there for screen readers only. */
export const HiddenTitle: Story = {
  render: () => (
    <Dialog defaultOpen>
      <DialogTrigger asChild>
        <Button variant="outline">Preview the CV</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader className="sr-only">
          <DialogTitle>CV preview, page 1 of 2</DialogTitle>
          <DialogDescription>
            CV for Senior data engineer, Lumen Freight
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2 pr-6 text-sm">
          <span className="text-lg font-semibold">Camille Martin</span>
          <span className="text-muted-foreground">
            Senior data engineer, Lyon. camille.martin@example.org
          </span>
          <p>
            Eight years building data pipelines for logistics and retail, most
            recently at Orchard Rail, where the team moved 40 depots onto one
            warehouse.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  ),
  parameters: OPEN_FOR_REVIEW,
};
