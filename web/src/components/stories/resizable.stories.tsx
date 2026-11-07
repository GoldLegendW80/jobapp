import type { Meta, StoryObj } from "@storybook/react-vite";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";

const POSTINGS = [
  ["Senior data engineer", "Lumen Freight, Lyon", 86],
  ["Analytics engineer", "Atelier Brume, Paris", 74],
  ["Data platform lead", "Orbe Santé, Remote", 68],
  ["Machine learning engineer", "Nordlys, Grenoble", 52],
  ["Backend engineer, Python", "Tramway Labs, Lyon", 47],
] as const;

function List() {
  return (
    <ul aria-label="Postings" className="flex flex-col gap-2">
      {POSTINGS.map(([title, where, fit], index) => (
        <li key={title}>
          <Card
            className="gap-1 px-4 py-3 data-[current=true]:ring-2 data-[current=true]:ring-primary"
            data-current={index === 0}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium">{title}</span>
              <Badge variant="secondary">Fit {fit}</Badge>
            </span>
            <span className="text-xs text-muted-foreground">{where}</span>
          </Card>
        </li>
      ))}
    </ul>
  );
}

function Detail() {
  return (
    <Card className="h-full">
      <CardContent className="flex max-w-prose flex-col gap-3 text-sm">
        <h2 className="text-xl font-medium">Senior data engineer</h2>
        <p className="text-muted-foreground">Lumen Freight, Lyon, Hybrid</p>
        <p>
          You own the pipelines that bring depot data into the warehouse:
          Airflow for orchestration, dbt for the models, Python where SQL is not
          enough.
        </p>
      </CardContent>
    </Card>
  );
}

const meta = {
  title: "Components/Data display/Resizable",
  component: ResizablePanelGroup,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Panes side by side whose border the person drags to share the width: the jobs list and the selected posting, from 1024 px. The handle is a separator: arrow keys move it too. Below 1024 px there is one pane, and no handle.",
      },
    },
  },
} satisfies Meta<typeof ResizablePanelGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The jobs list beside the selected posting. Drag the handle, or focus it and use the arrows. */
export const Typical: Story = {
  render: () => (
    <ResizablePanelGroup orientation="horizontal" className="h-96 max-w-5xl">
      <ResizablePanel defaultSize="35%" minSize="25%" maxSize="50%">
        <div className="h-full overflow-y-auto p-1 pr-2">
          <List />
        </div>
      </ResizablePanel>
      <ResizableHandle withHandle aria-label="Width of the list" />
      <ResizablePanel minSize="40%">
        <div className="h-full pl-2">
          <Detail />
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  ),
};

/** Without the grip: a thin line that shows its colour on hover and focus. */
export const PlainHandle: Story = {
  render: () => (
    <ResizablePanelGroup orientation="horizontal" className="h-80 max-w-5xl">
      <ResizablePanel defaultSize="35%">
        <div className="h-full overflow-y-auto p-1 pr-2">
          <List />
        </div>
      </ResizablePanel>
      <ResizableHandle aria-label="Width of the list" />
      <ResizablePanel>
        <div className="h-full pl-2">
          <Detail />
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  ),
};

/** Stacked: a posting over its documents, the border between them dragged up or down. */
export const Vertical: Story = {
  render: () => (
    <ResizablePanelGroup orientation="vertical" className="h-96 max-w-xl">
      <ResizablePanel defaultSize="60%">
        <div className="h-full pb-2">
          <Detail />
        </div>
      </ResizablePanel>
      <ResizableHandle withHandle aria-label="Height of the posting" />
      <ResizablePanel>
        <div className="h-full pt-2">
          <Card className="h-full">
            <CardContent className="text-sm text-muted-foreground">
              CV and letter for this posting.
            </CardContent>
          </Card>
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  ),
};
