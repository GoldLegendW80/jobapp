import type { Meta, StoryObj } from "@storybook/react-vite";
import { PlusIcon, SearchIcon } from "lucide-react";
import { MemoryRouter } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { Brand } from "@/components/blocks/brand";
import { PageHeader } from "@/components/blocks/page-header";
import { PillNav, PillNavLink } from "@/components/blocks/pill-nav";

const meta = {
  title: "Components/Blocks",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The few shared pieces pages are built from, above the shadcn components: the header of every page, the app's navigation and its mark.",
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
