import type { Meta, StoryObj } from "@storybook/react-vite";
import { Link, MemoryRouter } from "react-router";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";

const meta = {
  title: "Components/Navigation/Breadcrumb",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "shadcn's Breadcrumb: where a page sits below another one, each step a link back. The page header shows it above the title of a page below another (a posting below Jobs, an agent below Admin).",
      },
    },
  },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="rounded-xl bg-background p-6">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** One step up: the section, then the page itself. */
export const Typical: Story = {
  render: () => (
    <Breadcrumb>
      <BreadcrumbList>
        <BreadcrumbItem>
          <BreadcrumbLink asChild>
            <Link to="/jobs">Jobs</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbPage>Senior data engineer</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  ),
};

/** Two steps up: a CV of a posting. A long last step is cut, the full name in its title. */
export const TwoStepsLongName: Story = {
  name: "Two steps, a long name",
  render: () => (
    <Breadcrumb>
      <BreadcrumbList>
        <BreadcrumbItem>
          <BreadcrumbLink asChild>
            <Link to="/jobs">Jobs</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbLink asChild>
            <Link to="/jobs/42">
              Data platform engineer, streaming and real time pricing
            </Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbPage>CV</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  ),
};
