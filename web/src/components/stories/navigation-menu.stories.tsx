import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router";

import { Badge } from "@/components/ui/badge";

import { PillNav, PillNavLink } from "@/components/blocks/pill-nav";

const meta = {
  title: "Components/Navigation/Navigation menu",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "shadcn's NavigationMenu, styled as the app's sections: one white pill, the current section black and marked `aria-current=\"page\"`. The header uses it through PillNav, the phone's tab bar directly (app/layouts/bottom-nav.tsx).",
      },
    },
  },
  decorators: [
    (Story, { parameters }) => (
      <MemoryRouter initialEntries={[parameters.at ?? "/jobs"]}>
        <div className="rounded-xl bg-background p-6">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const sections = (
  <>
    <PillNavLink to="/jobs">Jobs</PillNavLink>
    <PillNavLink to="/discover">Discover</PillNavLink>
    <PillNavLink to="/applications">
      Applications
      <Badge variant="brand">3</Badge>
    </PillNavLink>
    <PillNavLink to="/profile">Profile</PillNavLink>
  </>
);

/** The app's sections, Jobs current. */
export const Typical: Story = {
  render: () => (
    <PillNav label="Main" className="w-fit">
      {sections}
    </PillNav>
  ),
};

/** Below a section's page the section stays current: a posting keeps Jobs black. */
export const OnAPageBelow: Story = {
  name: "On a page below a section",
  parameters: { at: "/jobs/42" },
  render: Typical.render,
};

/** Narrow: the pill scrolls sideways, it never wraps. */
export const Narrow: Story = {
  render: () => (
    <div className="max-w-64">
      <PillNav label="Main">{sections}</PillNav>
    </div>
  ),
};
