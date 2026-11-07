import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  ArrowUpRightIcon,
  BrainIcon,
  CircleCheckIcon,
  CircleXIcon,
  FileTextIcon,
  GlobeIcon,
  ListChecksIcon,
  WrenchIcon,
} from "lucide-react";

import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace";

const RESULT = `{
  "title": "Senior data engineer",
  "company": "Lumen Freight",
  "location": "Lyon, Hybrid",
  "salary": "55,000 to 65,000 EUR",
  "skills": ["Python", "SQL", "Airflow", "dbt", "Kafka", "Spark"]
}`;

const meta = {
  title: "Components/Data display/Trace",
  component: Trace,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What an agent did, one line per step: it thought, called a tool, read a page, answered. A line that has more (a tool's result, its arguments) opens in place; a line that leads elsewhere is a link. The run's own page and the chat show steps this way. status running, error or pending says where a step is, in words as well as colour.",
      },
    },
  },
} satisfies Meta<typeof Trace>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A finished run: it thought, read the posting with a tool (open its result), and answered. */
export const Typical: Story = {
  render: () => (
    <Trace className="max-w-lg">
      <TraceLine>
        <TraceIcon>
          <BrainIcon />
        </TraceIcon>
        <TraceTitle>Thought about the posting</TraceTitle>
        <TraceDescription>4 s</TraceDescription>
      </TraceLine>
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine>
            <TraceIcon>
              <WrenchIcon />
            </TraceIcon>
            <TraceTitle>Read the posting</TraceTitle>
            <TraceDescription>412 characters</TraceDescription>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <pre className="max-h-64 overflow-auto font-mono text-xs whitespace-pre-wrap">
            {RESULT}
          </pre>
        </TraceCollapsibleContent>
      </TraceCollapsible>
      <TraceLine>
        <TraceIcon>
          <CircleCheckIcon />
        </TraceIcon>
        <TraceTitle>Answered</TraceTitle>
      </TraceLine>
    </Trace>
  ),
};

/** Every status: done (none), running, failed, and a step that has not started. */
export const Statuses: Story = {
  render: () => (
    <Trace className="max-w-lg">
      <TraceLine>
        <TraceIcon>
          <CircleCheckIcon />
        </TraceIcon>
        <TraceTitle>Read your profile</TraceTitle>
      </TraceLine>
      <TraceLine status="running">
        <TraceIcon>
          <WrenchIcon />
        </TraceIcon>
        <TraceTitle>Running: filling the application form</TraceTitle>
      </TraceLine>
      <TraceLine status="error">
        <TraceIcon>
          <CircleXIcon />
        </TraceIcon>
        <TraceTitle>Failed to run: the site did not answer</TraceTitle>
      </TraceLine>
      <TraceLine status="pending">
        <TraceIcon>
          <ListChecksIcon />
        </TraceIcon>
        <TraceTitle>Check the answers before you approve</TraceTitle>
      </TraceLine>
    </Trace>
  ),
};

/** Sources: one summary line that opens on a link per page read, each to a new tab. */
export const Sources: Story = {
  render: () => (
    <Trace className="max-w-lg">
      <TraceCollapsible defaultOpen>
        <TraceCollapsibleTrigger>
          <TraceLine>
            <TraceIcon>
              <GlobeIcon />
            </TraceIcon>
            <TraceTitle>Read 2 pages</TraceTitle>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <Trace>
            {[
              ["Lumen Freight, careers", "https://jobs.example.org/lumen"],
              ["Lumen Freight, about", "https://example.org/about"],
            ].map(([title, url]) => (
              <a key={url} href={url} target="_blank" rel="noreferrer">
                <TraceLine>
                  <TraceIcon>
                    <FileTextIcon />
                  </TraceIcon>
                  <TraceTitle>
                    {title}
                    <span className="sr-only"> (opens in a new tab)</span>
                  </TraceTitle>
                  <TraceIcon>
                    <ArrowUpRightIcon />
                  </TraceIcon>
                </TraceLine>
              </a>
            ))}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  ),
};

/** A long title is cut with an ellipsis; a long result scrolls inside its fold. */
export const LongContent: Story = {
  render: () => (
    <Trace className="max-w-xs">
      <TraceCollapsible defaultOpen>
        <TraceCollapsibleTrigger>
          <TraceLine>
            <TraceIcon>
              <WrenchIcon />
            </TraceIcon>
            <TraceTitle>
              Read the application form of Lumen Freight on its careers site
            </TraceTitle>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <pre className="max-h-40 overflow-auto font-mono text-xs whitespace-pre-wrap">
            {Array.from({ length: 6 }, () => RESULT).join("\n")}
          </pre>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  ),
};

/** No step yet: say so in words, not with an empty trace. */
export const Empty: Story = {
  render: () => <p className="text-sm text-muted-foreground">No step yet.</p>,
};
