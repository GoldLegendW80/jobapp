import type { Meta, StoryObj } from "@storybook/react-vite";
import { HistoryIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

// Shown in its own iframe on the docs page, so the open sheet does not cover the page.
const OPEN_FOR_REVIEW = {
  docs: { story: { inline: false, height: "420px" } },
} as const;

const POSTING = [
  "Lumen Freight moves goods for 300 shops across France from 40 depots. The data team turns what every truck and pallet records into the plans the depots work from each morning.",
  "You own the pipelines that bring depot data into the warehouse: Airflow for orchestration, dbt for the models, Python where SQL is not enough.",
  "You work with two analysts and a product manager, review their models and help them write faster queries.",
  "What we ask: five years of data engineering, Python and SQL you are proud of, and pipelines you have kept running for more than a year.",
  "What we offer: 55,000 to 65,000 EUR a year, two days a week at home, a train pass paid in full, and a week each year to learn what you choose.",
];

function PostingSheet({
  defaultOpen = false,
  long = false,
}: {
  defaultOpen?: boolean;
  long?: boolean;
}) {
  return (
    <Sheet defaultOpen={defaultOpen}>
      <SheetTrigger asChild>
        <Button variant="outline">Open the posting</Button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Senior data engineer</SheetTitle>
          <SheetDescription>Lumen Freight, Lyon, Hybrid</SheetDescription>
        </SheetHeader>
        <div
          tabIndex={0}
          role="region"
          aria-label="Posting"
          className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Fit 86</Badge>
            <span>55,000 to 65,000 EUR a year, permanent</span>
          </div>
          <p className="text-muted-foreground">
            You match 7 of its 9 skills: <mark>Python</mark>,{" "}
            <mark>Airflow</mark>, <mark>dbt</mark> and 4 more.
          </p>
          <Separator />
          {(long ? [...POSTING, ...POSTING] : POSTING.slice(0, 2)).map(
            (paragraph, index) => (
              <p key={`${String(index)}-${paragraph.slice(0, 16)}`}>
                {paragraph}
              </p>
            ),
          )}
        </div>
        <SheetFooter className="flex-row border-t">
          <Button className="flex-1">Apply</Button>
          <SheetClose asChild>
            <Button variant="outline" className="flex-1">
              Pass
            </Button>
          </SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

const meta = {
  title: "Components/Overlays/Sheet",
  component: Sheet,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A panel from the edge of the screen for details the person reads beside their work: a posting from the deck, search options, the chat about a posting, the history of the profile. It keeps the page in view; use Dialog for a short task, a page for a long one.",
      },
    },
  },
} satisfies Meta<typeof Sheet>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting from the deck, from the right: title, the facts, the text, and its two choices. Click to open. */
export const Typical: Story = {
  render: () => <PostingSheet />,
};

/** Open, for review. */
export const Open: Story = {
  render: () => <PostingSheet defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** Right for details (the default), left for navigation, bottom for options on a phone, top rarely. */
export const Sides: Story = {
  render: () => (
    <div className="flex flex-wrap gap-3">
      {(["right", "left", "bottom", "top"] as const).map((side) => (
        <Sheet key={side}>
          <SheetTrigger asChild>
            <Button variant="outline">Open from the {side}</Button>
          </SheetTrigger>
          <SheetContent side={side}>
            <SheetHeader>
              <SheetTitle>Search options</SheetTitle>
              <SheetDescription>
                Data engineering roles in Lyon, Paris or remote, from 50,000 EUR
                a year.
              </SheetDescription>
            </SheetHeader>
          </SheetContent>
        </Sheet>
      ))}
    </div>
  ),
};

/** From the bottom, as search options open on a phone. */
export const FromBottom: Story = {
  render: () => (
    <Sheet defaultOpen>
      <SheetTrigger asChild>
        <Button variant="outline">Search options</Button>
      </SheetTrigger>
      <SheetContent side="bottom">
        <SheetHeader>
          <SheetTitle>Search options</SheetTitle>
          <SheetDescription>
            Data engineering roles in Lyon, Paris or remote, from 50,000 EUR a
            year.
          </SheetDescription>
        </SheetHeader>
        <SheetFooter className="flex-row">
          <Button className="flex-1">Find new postings</Button>
          <SheetClose asChild>
            <Button variant="outline" className="flex-1">
              Close
            </Button>
          </SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** Long content scrolls between a fixed header and footer, so Apply stays in reach. */
export const LongContent: Story = {
  render: () => <PostingSheet defaultOpen long />,
  parameters: OPEN_FOR_REVIEW,
};

/** Nothing to show yet: an Empty in the panel says what will appear. */
export const EmptyState: Story = {
  name: "Empty",
  render: () => (
    <Sheet defaultOpen>
      <SheetTrigger asChild>
        <Button variant="outline">
          <HistoryIcon />
          History
        </Button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>History of your profile</SheetTitle>
          <SheetDescription>
            Every change, by you or the assistant.
          </SheetDescription>
        </SheetHeader>
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <HistoryIcon />
            </EmptyMedia>
            <EmptyTitle>No changes yet</EmptyTitle>
            <EmptyDescription>
              When you or the assistant change your profile, each change shows
              here and you can undo it.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </SheetContent>
    </Sheet>
  ),
  parameters: OPEN_FOR_REVIEW,
};
