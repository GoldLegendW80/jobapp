import type { Meta, StoryObj } from "@storybook/react-vite";
import { SearchIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

// Shown in its own iframe on the docs page, so the open drawer does not cover the page.
const OPEN_FOR_REVIEW = {
  docs: { story: { inline: false, height: "560px" } },
} as const;

const POSTING = [
  "Lumen Freight moves goods for 300 shops across France from 40 depots. The data team turns what every truck and pallet records into the plans the depots work from each morning.",
  "You own the pipelines that bring depot data into the warehouse: Airflow for orchestration, dbt for the models, Python where SQL is not enough.",
  "You work with two analysts and a product manager, review their models and help them write faster queries.",
  "What we ask: five years of data engineering, Python and SQL you are proud of, and pipelines you have kept running for more than a year.",
  "What we offer: 55,000 to 65,000 EUR a year, two days a week at home, a train pass paid in full, and a week each year to learn what you choose.",
];

function PostingDrawer({
  defaultOpen = false,
  long = false,
}: {
  defaultOpen?: boolean;
  long?: boolean;
}) {
  return (
    <Drawer defaultOpen={defaultOpen}>
      <DrawerTrigger asChild>
        <Button variant="outline">Read the posting</Button>
      </DrawerTrigger>
      <DrawerContent>
        <DrawerHeader>
          <DrawerTitle>Senior data engineer</DrawerTitle>
          <DrawerDescription>Lumen Freight, Lyon, Hybrid</DrawerDescription>
        </DrawerHeader>
        <div
          tabIndex={0}
          role="region"
          aria-label="Posting"
          className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 text-sm leading-relaxed outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <Badge variant="secondary" className="w-fit">
            Fit 86
          </Badge>
          {(long ? [...POSTING, ...POSTING] : POSTING.slice(0, 2)).map(
            (paragraph, index) => (
              <p key={`${String(index)}-${paragraph.slice(0, 16)}`}>
                {paragraph}
              </p>
            ),
          )}
        </div>
        <DrawerFooter className="flex-row">
          <Button className="flex-1">Like</Button>
          <DrawerClose asChild>
            <Button variant="outline" className="flex-1">
              Close
            </Button>
          </DrawerClose>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}

const meta = {
  title: "Components/Overlays/Drawer",
  component: Drawer,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A sheet that rises from the bottom of a phone, with a handle to drag it down: a posting read from the deck, the setup of Find jobs, filters. On a wide screen the same content opens in a Dialog or a Sheet; the drawer is the phone's version of them.",
      },
    },
  },
} satisfies Meta<typeof Drawer>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting from the deck: its title, the text, Like and Close. Click to open, drag down to close. */
export const Typical: Story = {
  render: () => <PostingDrawer />,
};

/** Open, for review. */
export const Open: Story = {
  render: () => <PostingDrawer defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** Long content scrolls inside, between the header and the footer that stay. */
export const LongContent: Story = {
  render: () => <PostingDrawer defaultOpen long />,
  parameters: OPEN_FOR_REVIEW,
};

/** A short form in a drawer: the fields, then the action at the bottom, in reach of a thumb. */
export const WithAForm: Story = {
  render: () => (
    <Drawer defaultOpen>
      <DrawerTrigger asChild>
        <Button>
          <SearchIcon />
          Find jobs
        </Button>
      </DrawerTrigger>
      <DrawerContent>
        <DrawerHeader>
          <DrawerTitle>Find jobs</DrawerTitle>
          <DrawerDescription>
            Say what you want, and start. The app keeps the postings that match.
          </DrawerDescription>
        </DrawerHeader>
        <form
          className="px-4"
          onSubmit={(event) => {
            event.preventDefault();
          }}
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="drawer-titles">Job titles</FieldLabel>
              <Input id="drawer-titles" defaultValue="Data engineer" />
            </Field>
            <Field>
              <FieldLabel htmlFor="drawer-places">Places</FieldLabel>
              <Input id="drawer-places" defaultValue="Lyon, Paris" />
            </Field>
          </FieldGroup>
        </form>
        <DrawerFooter>
          <Button>Save and start search</Button>
          <DrawerClose asChild>
            <Button variant="outline">Cancel</Button>
          </DrawerClose>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** From the right, where a phone in landscape has more width than height. */
export const FromTheRight: Story = {
  render: () => (
    <Drawer direction="right">
      <DrawerTrigger asChild>
        <Button variant="outline">Open from the right</Button>
      </DrawerTrigger>
      <DrawerContent>
        <DrawerHeader>
          <DrawerTitle>Search options</DrawerTitle>
          <DrawerDescription>
            Data engineering roles in Lyon, Paris or remote.
          </DrawerDescription>
        </DrawerHeader>
      </DrawerContent>
    </Drawer>
  ),
};
