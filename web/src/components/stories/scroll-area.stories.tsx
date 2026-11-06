import type { Meta, StoryObj } from "@storybook/react-vite";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";

const POSTING = [
  "Lumen Freight moves goods for 300 shops across France from 40 depots. Every truck, pallet and delivery leaves a trace, and the data team turns those traces into the plans the depots work from each morning.",
  "As a senior data engineer, you own the pipelines that bring depot data into the warehouse: Airflow for orchestration, dbt for the models, Python where SQL is not enough. You decide how they are tested and monitored.",
  "You work with two analysts and a product manager. You review their models, help them write faster queries, and say no to a dashboard when a table would do.",
  "What we ask: five years of data engineering, Python and SQL you are proud of, and pipelines you have kept running for more than a year. French or English, both are spoken in the team.",
  "What we offer: 55,000 to 65,000 EUR a year, two days a week at home, a train pass paid in full, and a week each year to learn what you choose.",
];

const SKILLS = [
  "Python",
  "SQL",
  "Airflow",
  "dbt",
  "PostgreSQL",
  "BigQuery",
  "Docker",
  "Terraform",
  "Kafka",
  "Spark",
  "Great Expectations",
  "Looker",
];

const RUN_LOG = Array.from({ length: 30 }, (_, index) => {
  const steps = [
    "Opened the application form",
    "Filled First name and Last name",
    "Filled E-mail with camille.martin@example.org",
    "Uploaded the CV for this posting",
    "Answered Why do you want to join us",
    "Waited for the page to load",
  ];
  return `14:${String(32 + Math.floor(index / 3)).padStart(2, "0")}  ${steps[index % steps.length]}`;
});

const meta = {
  title: "Components/Data display/Scroll area",
  component: ScrollArea,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A region of fixed size that scrolls on its own, with a thin scrollbar in the theme: a long posting beside its actions, a run's log, a row of skills. The page itself scrolls natively; use this only for a region inside it.",
      },
    },
  },
} satisfies Meta<typeof ScrollArea>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting's full text in a fixed height, so its actions stay in view. */
export const Typical: Story = {
  render: () => (
    <Card className="max-w-md overflow-hidden py-0">
      <ScrollArea className="h-72">
        <div className="flex flex-col gap-3 p-4 text-sm">
          <h3 className="font-semibold">Senior data engineer</h3>
          {POSTING.map((paragraph) => (
            <p key={paragraph.slice(0, 20)}>{paragraph}</p>
          ))}
        </div>
      </ScrollArea>
    </Card>
  ),
};

/** Sideways: add a horizontal ScrollBar for a row that must stay on one line. */
export const Horizontal: Story = {
  render: () => (
    <Card className="max-w-sm overflow-hidden py-0">
      <ScrollArea className="whitespace-nowrap">
        <div className="flex w-max gap-2 p-3">
          {SKILLS.map((skill) => (
            <Badge key={skill} variant="outline">
              {skill}
            </Badge>
          ))}
        </div>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </Card>
  ),
};

/** Many lines, such as the steps of a run: the region scrolls, the page around it does not. */
export const ManyItems: Story = {
  render: () => (
    <Card className="max-w-md overflow-hidden py-0">
      <ScrollArea className="h-64">
        <div className="flex flex-col p-4 text-sm">
          <h3 className="mb-2 font-medium">Filling an application form</h3>
          {RUN_LOG.map((line, index) => (
            <div key={`${line}-${String(index)}`}>
              {index > 0 ? <Separator className="my-2" /> : null}
              <span className="font-mono text-xs text-muted-foreground">
                {line}
              </span>
            </div>
          ))}
        </div>
      </ScrollArea>
    </Card>
  ),
};

/** Content shorter than the region: no scrollbar shows, the region keeps its height. */
export const ShortContent: Story = {
  render: () => (
    <Card className="max-w-md overflow-hidden py-0">
      <ScrollArea className="h-40">
        <p className="p-4 text-sm">
          Lumen Freight moves goods for 300 shops across France from 40 depots.
        </p>
      </ScrollArea>
    </Card>
  ),
};
