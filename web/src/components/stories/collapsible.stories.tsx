import type { Meta, StoryObj } from "@storybook/react-vite";
import { ChevronDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";

const DETAILS = [
  ["Contract", "Permanent"],
  ["Work mode", "Hybrid, two days a week at home"],
  ["Team", "Data, 6 people"],
  ["Found on", "Welcome to the Jungle, 2 November"],
] as const;

function PostingDetails() {
  return (
    <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-2 pt-2 text-sm">
      {DETAILS.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-muted-foreground">{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Trigger({ children }: { children: string }) {
  return (
    <CollapsibleTrigger asChild>
      <Button
        variant="ghost"
        className="-ml-4 self-start [&[data-state=open]>svg]:rotate-180"
      >
        {children}
        <ChevronDownIcon className="transition-transform" />
      </Button>
    </CollapsibleTrigger>
  );
}

const meta = {
  title: "Components/Data display/Collapsible",
  component: Collapsible,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Folds away detail most people skip, under a trigger that says what it holds: more about a posting, the steps of a run, advanced options. Closed by default; what the person needs to decide stays outside it.",
      },
    },
  },
} satisfies Meta<typeof Collapsible>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Closed: the posting page folds its secondary facts under More about this posting. */
export const Typical: Story = {
  render: () => (
    <Collapsible className="max-w-md">
      <Trigger>More about this posting</Trigger>
      <CollapsibleContent>
        <PostingDetails />
      </CollapsibleContent>
    </Collapsible>
  ),
};

/** Open, as it shows after a click. */
export const Open: Story = {
  render: () => (
    <Collapsible defaultOpen className="max-w-md">
      <Trigger>More about this posting</Trigger>
      <CollapsibleContent>
        <PostingDetails />
      </CollapsibleContent>
    </Collapsible>
  ),
};

/** A summary stays visible above the trigger; only the detail folds. */
export const WithSummary: Story = {
  render: () => (
    <Card className="max-w-md gap-0 p-4">
      <Collapsible className="flex flex-col gap-2">
        <div className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Reading your documents</span>
          <span className="text-muted-foreground">
            Finished yesterday at 18:05. 41 facts proposed for your profile.
          </span>
        </div>
        <Trigger>Show the 6 steps</Trigger>
        <CollapsibleContent>
          <ol className="flex list-decimal flex-col gap-1 pl-5 text-sm">
            <li>Read Base CV, two pages</li>
            <li>Read LinkedIn data export</li>
            <li>Read a scanned page of a reference letter</li>
            <li>Found 23 skills and 8 roles</li>
            <li>Matched them against your profile</li>
            <li>Proposed 41 facts for your review</li>
          </ol>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  ),
};

/** Several sections in a column: each opens on its own, a separator between them. */
export const Many: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-2">
      {["More about this posting", "Questions on the form", "Advanced"].map(
        (label, index) => (
          <div key={label} className="flex flex-col gap-2">
            {index > 0 ? <Separator /> : null}
            <Collapsible defaultOpen={index === 0}>
              <Trigger>{label}</Trigger>
              <CollapsibleContent>
                <PostingDetails />
              </CollapsibleContent>
            </Collapsible>
          </div>
        ),
      )}
    </div>
  ),
};

/** Long content: the page grows to hold it, nothing scrolls inside the fold. */
export const LongContent: Story = {
  render: () => (
    <Collapsible defaultOpen className="max-w-md">
      <Trigger>Why this fit score</Trigger>
      <CollapsibleContent className="flex flex-col gap-2 pt-2 text-sm">
        <p>
          The posting asks for 9 skills and you have 7 of them: Python, SQL,
          Airflow, dbt, PostgreSQL, Docker and Terraform. Kafka and Spark are
          missing from your profile.
        </p>
        <p>
          The role is senior and so are you, with eight years in data
          engineering. The pay is within your range, and Lyon is one of your
          places, so neither takes points off.
        </p>
        <p>
          It asks for French or English; you have both. Nothing in the posting
          matches your roles to never apply for.
        </p>
      </CollapsibleContent>
    </Collapsible>
  ),
};
