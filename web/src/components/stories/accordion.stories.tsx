import type { Meta, StoryObj } from "@storybook/react-vite";
import { ArrowDownIcon, ArrowUpIcon, Trash2Icon } from "lucide-react";

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const BLOCKS = [
  [
    "Lumen Freight, 2021 to now",
    "Senior data engineer. I own the pipelines that bring the data of 40 depots into the warehouse: Airflow, dbt, Python. I cut the morning load from 3 hours to 40 minutes.",
  ],
  [
    "Atelier Brume, 2017 to 2021",
    "Data engineer, then lead of a team of three. Built the first warehouse of the company on PostgreSQL and moved it to BigQuery.",
  ],
  [
    "Languages",
    "French, native. English, fluent: I worked in English for four years. Spanish, basic.",
  ],
] as const;

const meta = {
  title: "Components/Data display/Accordion",
  component: Accordion,
  tags: ["autodocs"],
  args: { type: "multiple" },
  parameters: {
    docs: {
      description: {
        component:
          "A list of named sections that each open in place: the blocks of the fact base, the questions of a form. type multiple lets several stay open; single closes the others. One section on its own is a Collapsible, not an accordion.",
      },
    },
  },
} satisfies Meta<typeof Accordion>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The blocks of the fact base: several can be open at once, the first is. */
export const Typical: Story = {
  render: () => (
    <Card className="max-w-xl py-2">
      <CardContent>
        <Accordion type="multiple" defaultValue={["block-0"]}>
          {BLOCKS.map(([title, body], index) => (
            <AccordionItem key={title} value={`block-${String(index)}`}>
              <AccordionTrigger>{title}</AccordionTrigger>
              <AccordionContent className="text-muted-foreground">
                {body}
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </CardContent>
    </Card>
  ),
};

/** One open at a time: opening a section closes the one before. */
export const Single: Story = {
  render: () => (
    <Card className="max-w-xl py-2">
      <CardContent>
        <Accordion type="single" collapsible defaultValue="block-1">
          {BLOCKS.map(([title, body], index) => (
            <AccordionItem key={title} value={`block-${String(index)}`}>
              <AccordionTrigger>{title}</AccordionTrigger>
              <AccordionContent>{body}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </CardContent>
    </Card>
  ),
};

/** Actions beside each section's title, outside its trigger: move up, move down, delete. */
export const WithActions: Story = {
  render: () => (
    <Card className="max-w-xl py-2">
      <CardContent>
        <Accordion type="multiple">
          {BLOCKS.map(([title, body], index) => (
            <AccordionItem key={title} value={`block-${String(index)}`}>
              <div className="flex items-center gap-1">
                <AccordionTrigger>{title}</AccordionTrigger>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Move ${title} up`}
                  disabled={index === 0}
                >
                  <ArrowUpIcon />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Move ${title} down`}
                  disabled={index === BLOCKS.length - 1}
                >
                  <ArrowDownIcon />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Delete ${title}`}
                >
                  <Trash2Icon />
                </Button>
              </div>
              <AccordionContent>{body}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </CardContent>
    </Card>
  ),
};

/** A section that cannot open yet is disabled, and its title says why. */
export const Disabled: Story = {
  render: () => (
    <Card className="max-w-xl py-2">
      <CardContent>
        <Accordion type="multiple">
          <AccordionItem value="ready">
            <AccordionTrigger>Questions on the form</AccordionTrigger>
            <AccordionContent>
              Your notice period, your right to work in France, and a link to
              your portfolio.
            </AccordionContent>
          </AccordionItem>
          <AccordionItem value="later" disabled>
            <AccordionTrigger>
              Answers the agent wrote (once the form is read)
            </AccordionTrigger>
            <AccordionContent>Not yet.</AccordionContent>
          </AccordionItem>
        </Accordion>
      </CardContent>
    </Card>
  ),
};

/** On a phone, long titles wrap and the chevron stays at the end. */
export const OnAPhone: Story = {
  render: () => (
    <Card className="max-w-sm py-2">
      <CardContent>
        <Accordion type="multiple" defaultValue={["block-0"]}>
          {BLOCKS.map(([title, body], index) => (
            <AccordionItem key={title} value={`block-${String(index)}`}>
              <AccordionTrigger>
                {title}, and what I did there in more words
              </AccordionTrigger>
              <AccordionContent>{body}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </CardContent>
    </Card>
  ),
};
