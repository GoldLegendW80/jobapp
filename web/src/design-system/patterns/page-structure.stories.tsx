import type { Meta, StoryObj } from "@storybook/react-vite";
import { Bot, Download, ExternalLink, Search, Send } from "lucide-react";
import { MemoryRouter } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { EmptyState } from "@/components/blocks/empty-state";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { PageTabs } from "@/components/blocks/page-tabs";
import { Section } from "@/components/blocks/section";

const meta = {
  title: "Patterns/Page structure",
  parameters: {
    layout: "fullscreen",
    docs: {
      description: {
        component: [
          "Every signed-in page has the same structure, top to bottom: **PageHeader** (the breadcrumb of the pages above on a page below another, the h1, one line on what the page is for, its actions), **PageTabs** when the area has several views, then **PageBody** made of **Sections**.",
          "",
          "- **Width.** Every page spans the shell, 1440 px at most. Lines of text stay short (65 characters); the width goes to columns, never to longer lines.",
          "- **Three layouts.** A workspace (lists, tables, maps, grids) uses the whole body. An item (a posting, a run, a document) puts its content in the main column and what accompanies it (status, actions, facts) in the side rail. Settings put each group's name and purpose on the left and its controls on the right.",
          "- **Actions.** One primary action per page header and per section, first. Two other actions at most beside it; anything else goes in a menu. Labels say what happens: Find jobs, Approve and submit.",
          "- **Surfaces.** A section's content sits on one white surface. Never a card inside a card: inside one, groups are told apart by space and a small heading, not by borders.",
          "- **States.** Loading keeps the layout (skeletons or a line with a spinner); an error names what failed and offers Retry (LoadError); an empty view says what is missing and holds the action that fills it (EmptyState).",
        ].join("\n"),
      },
    },
  },
  decorators: [
    (Story, { parameters }) => (
      <MemoryRouter initialEntries={[parameters.at ?? "/"]}>
        <div className="min-h-dvh bg-background px-4 py-10 sm:px-6 lg:px-8">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const ROWS = [
  ["Senior data engineer", "Lumen Freight", "Lyon", "91"],
  ["Analytics engineer", "Kestrel Health", "Remote, France", "84"],
  ["Machine learning engineer", "Orpheo Maps", "Grenoble", "72"],
  ["Product analyst", "Maison Tilleul", "Bordeaux", "58"],
] as const;

/** A workspace: the header and its views, then one surface that uses the whole width. */
export const Workspace: Story = {
  parameters: { at: "/jobs" },
  render: () => (
    <Page>
      <PageHeader
        title="Jobs"
        description="4 postings from 2 websites, scored against your profile"
        actions={
          <>
            <Button>
              <Search />
              Find jobs
            </Button>
            <Button variant="outline">Add a posting</Button>
          </>
        }
      />
      <PageTabs
        label="Jobs views"
        tabs={[
          { to: "/jobs", label: "List" },
          { to: "/jobs/map", label: "Map" },
        ]}
      >
        <PageBody>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Place</TableHead>
                <TableHead className="text-right">Fit</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ROWS.map(([title, company, place, fit]) => (
                <TableRow key={title}>
                  <TableCell className="font-medium">{title}</TableCell>
                  <TableCell>{company}</TableCell>
                  <TableCell>{place}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fit}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </PageBody>
      </PageTabs>
    </Page>
  ),
};

/** An item: its content in the main column, what accompanies it in the side rail; `lead` opens the rail and comes first on a phone. */
export const Item: Story = {
  render: () => (
    <Page>
      <PageHeader
        trail={[{ to: "/jobs", label: "Jobs" }]}
        title="Senior data engineer"
        description="Lumen Freight, Lyon, France, 55,000 to 65,000 EUR a year"
        actions={
          <>
            <Button>
              <Send />
              Apply
            </Button>
            <Button variant="outline">
              <Bot />
              Chat
            </Button>
          </>
        }
      />
      <PageBody
        lead={
          <Section title="Your application">
            <Card>
              <CardContent className="flex flex-col gap-3 text-sm">
                <p>No application yet.</p>
                <p className="text-muted-foreground">
                  The assistant fills the form and sends it. You can watch it
                  live here.
                </p>
              </CardContent>
            </Card>
          </Section>
        }
        aside={
          <>
            <Section title="Documents">
              <Card>
                <CardContent className="flex flex-col gap-3 text-sm">
                  <p className="flex items-center justify-between">
                    CV <Badge variant="success">Written</Badge>
                  </p>
                  <p className="flex items-center justify-between">
                    Letter <Badge variant="outline">Not written yet</Badge>
                  </p>
                  <Button variant="outline" size="sm" className="w-fit">
                    <Download />
                    Download the CV
                  </Button>
                </CardContent>
              </Card>
            </Section>
          </>
        }
      >
        <Section title="Posting">
          <Card>
            <CardContent className="flex max-w-prose flex-col gap-3 text-sm leading-relaxed">
              <p>
                Lumen Freight is hiring for this role in Lyon. The team is
                small: five people who own the data the rest of the company
                reads every morning.
              </p>
              <p>
                What you will do: build and run the pipelines the team relies
                on, and keep the platform simple and cheap to run.
              </p>
              <a
                href="https://careers.example.org"
                className="inline-flex w-fit items-center gap-1.5 underline underline-offset-4"
              >
                careers.example.org
                <ExternalLink aria-hidden="true" className="size-3.5" />
              </a>
            </CardContent>
          </Card>
        </Section>
        <Section title="Notes" description="Saved as you type.">
          <Card>
            <CardContent className="text-sm text-muted-foreground">
              What you learnt, who you talked to, what comes next.
            </CardContent>
          </Card>
        </Section>
      </PageBody>
    </Page>
  ),
};

/** Settings: each group's name and purpose on the left, its controls on the right. */
export const Settings: Story = {
  parameters: { at: "/settings/applications" },
  render: () => (
    <Page>
      <PageHeader
        title="Settings"
        description="What the search keeps and how the app applies for you"
      />
      <PageTabs
        label="Settings"
        tabs={[
          { to: "/settings/search", label: "Search" },
          { to: "/settings/applications", label: "Applications" },
        ]}
      >
        <PageBody>
          <Section
            layout="side"
            title="When you click Apply"
            description="Whether you check each filled form before it is sent."
          >
            <Card>
              <CardContent className="flex items-start gap-3">
                <Checkbox id="review" defaultChecked />
                <Label htmlFor="review" className="flex-col items-start">
                  Let me review each filled form before it is sent
                </Label>
              </CardContent>
            </Card>
          </Section>
          <Section
            layout="side"
            title="Pause"
            description="While paused, the app submits nothing for you."
          >
            <Card>
              <CardContent className="flex items-center gap-3">
                <Switch id="pause" />
                <Label htmlFor="pause">Pause automatic applications</Label>
              </CardContent>
            </Card>
          </Section>
          <Section
            layout="side"
            title="Apply automatically from"
            description="Full auto applies only to postings from this fit score."
          >
            <Card>
              <CardContent className="flex flex-wrap items-end gap-3">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="threshold">Fit score, 0 to 100</Label>
                  <Input id="threshold" defaultValue="80" className="w-28" />
                </div>
                <Button>Save threshold</Button>
              </CardContent>
            </Card>
          </Section>
        </PageBody>
      </PageTabs>
    </Page>
  ),
};

/** A view with nothing in it yet says what is missing and holds the action that fills it. */
export const EmptyWorkspace: Story = {
  name: "Workspace, empty",
  parameters: { at: "/jobs" },
  render: () => (
    <Page>
      <PageHeader title="Jobs" description="No posting yet" />
      <PageBody>
        <EmptyState
          icon={Search}
          title="No postings yet"
          description="Tell the app where to look and it brings postings here."
          actions={
            <>
              <Button>
                <Search />
                Find jobs
              </Button>
              <Button variant="outline">Add a posting by hand</Button>
            </>
          }
        />
      </PageBody>
    </Page>
  ),
};
