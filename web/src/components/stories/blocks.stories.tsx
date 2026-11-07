import type { Meta, StoryObj } from "@storybook/react-vite";
import { PlusIcon, SearchIcon, SearchX } from "lucide-react";
import { MemoryRouter } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { Brand } from "@/components/blocks/brand";
import { EmptyState } from "@/components/blocks/empty-state";
import { Fact, Facts } from "@/components/blocks/facts";
import { LoadError } from "@/components/blocks/load-error";
import { PageTabs } from "@/components/blocks/page-tabs";
import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/blocks/page-header";
import { PillNav, PillNavLink } from "@/components/blocks/pill-nav";

const meta = {
  title: "Components/Blocks",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The few shared pieces pages are built from, above the shadcn components: the header of every page, its views, its sections, the empty and failed states, the app's navigation and its mark. How they make a page: Patterns, Page structure.",
      },
    },
  },
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={["/jobs"]}>
        <div className="rounded-xl bg-background p-6">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The top of every page: its only h1, one line on what the page is for, and its actions, the primary one first. */
export const PageHeaderTypical: Story = {
  name: "Page header",
  render: () => (
    <PageHeader
      title="Jobs"
      description="240 postings from 6 websites, scored against your profile"
      actions={
        <>
          <Button>
            <SearchIcon />
            Find jobs
          </Button>
          <Button variant="secondary">
            <PlusIcon />
            Add a posting
          </Button>
        </>
      }
    />
  ),
};

/** A page with nothing to do from its header: the title alone. A long title wraps. */
export const PageHeaderTitleOnly: Story = {
  name: "Page header, title only",
  render: () => (
    <PageHeader title="Data platform engineer, streaming and real time pricing at Atelier Brume" />
  ),
};

/** The app's sections in one white pill; the current one is black. A count that waits for the person is orange. */
export const Navigation: Story = {
  render: () => (
    <PillNav label="Main" className="w-fit">
      <PillNavLink to="/jobs">Jobs</PillNavLink>
      <PillNavLink to="/discover">Discover</PillNavLink>
      <PillNavLink to="/applications">
        Applications
        <Badge variant="brand">3</Badge>
      </PillNavLink>
      <PillNavLink to="/profile">Profile</PillNavLink>
    </PillNav>
  ),
};

/** The mark and name, a link home. */
export const BrandMark: Story = {
  name: "Brand",
  render: () => <Brand to="/jobs" />,
};

/** A page below another one: the breadcrumb of the pages above, each a link, then this page. */
export const PageHeaderWithBreadcrumb: Story = {
  name: "Page header, with a breadcrumb",
  render: () => (
    <PageHeader
      trail={[{ to: "/jobs", label: "Jobs" }]}
      title="Senior data engineer"
      description="Lumen Freight, Lyon, France"
      actions={<Button>Apply</Button>}
    />
  ),
};

const DEEP_TRAIL = [
  { to: "/jobs", label: "Jobs" },
  {
    to: "/jobs/posting-1",
    label: "Data platform engineer, streaming and real time pricing",
  },
];

/** Two pages above, and long names: each ends in an ellipsis on one line, whole on hover; the h1 wraps. */
export const PageHeaderDeepTrail: Story = {
  name: "Page header, two pages above and long names",
  render: () => (
    <PageHeader
      trail={DEEP_TRAIL}
      title="CV for Data platform engineer, streaming and real time pricing at Atelier Brume"
    />
  ),
};

/** On a phone, only the way up: the last page above, with an arrow. */
export const PageHeaderOnAPhone: Story = {
  name: "Page header, with a breadcrumb, on a phone",
  globals: { viewport: { value: "mobile2", isRotated: false } },
  render: () => (
    <PageHeader
      trail={DEEP_TRAIL}
      title="CV for Data platform engineer, streaming and real time pricing at Atelier Brume"
    />
  ),
};

/** The views of one area, each at its own address: arrows move between them, Enter opens one. */
export const Views: Story = {
  render: () => (
    <PageTabs
      label="Jobs views"
      tabs={[
        { to: "/jobs", label: "List" },
        { to: "/jobs/map", label: "Map" },
      ]}
    >
      <p className="text-sm text-muted-foreground">The open view's content.</p>
    </PageTabs>
  ),
};

/** A part of a page: its h2, what it holds, its own action, then its content on one surface. */
export const SectionStacked: Story = {
  name: "Section",
  render: () => (
    <Section
      title="Invites"
      description="One-time links to create an account or reset a password."
      actions={<Button size="sm">Invite someone</Button>}
    >
      <Card>
        <CardContent className="text-sm">
          noor.haddad@example.org, expires in 7 days
        </CardContent>
      </Card>
    </Section>
  ),
};

/** Settings: what a group does on the left, its controls on the right (stacked on a phone). */
export const SectionSide: Story = {
  name: "Section, side heading",
  render: () => (
    <Section
      layout="side"
      title="Pause"
      description="While paused, the app submits nothing for you."
    >
      <Card>
        <CardContent className="text-sm">
          Pause automatic applications
        </CardContent>
      </Card>
    </Section>
  ),
};

/** Something could not be loaded: what, why, and Retry, which spins while it tries again. */
export const LoadFailed: Story = {
  name: "Load error",
  render: () => (
    <LoadError
      query={{
        error: new Error("The server did not answer."),
        isFetching: false,
        refetch: () => undefined,
      }}
    >
      The postings could not be loaded.
    </LoadError>
  ),
};

/** Nothing to show: what is missing, what to do, and the action that does it. */
export const NothingYet: Story = {
  name: "Empty state",
  render: () => (
    <EmptyState
      icon={SearchX}
      title="No posting matches"
      description="Clear a filter or search for something else."
      actions={<Button variant="outline">Clear the filters</Button>}
    />
  ),
};

/** Facts about one thing, a small label over each value: a posting's dates, a run's figures. */
export const FactsGrid: Story = {
  name: "Facts",
  render: () => (
    <Facts className="max-w-sm">
      <Fact label="Found">1 Nov 2026</Fact>
      <Fact label="Published">20 Oct 2026</Fact>
      <Fact label="Model">claude-sonnet-5-5</Fact>
      <Fact label="Duration">42 s</Fact>
    </Facts>
  ),
};
