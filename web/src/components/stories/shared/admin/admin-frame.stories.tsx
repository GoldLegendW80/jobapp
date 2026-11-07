import type { Meta, StoryObj } from "@storybook/react-vite";

import { Section } from "@/components/blocks/section";
import { AdminFrame } from "@/components/shared/admin/admin-frame";
import { Card, CardContent } from "@/components/ui/card";
import { withApp } from "@/design-system/with-app";

function Placeholder({ title, rows }: { title: string; rows: string[] }) {
  return (
    <Section title={title}>
      <Card>
        <CardContent>
          <ul className="flex flex-col divide-y text-sm">
            {rows.map((row) => (
              <li key={row} className="py-2">
                {row}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </Section>
  );
}

const meta = {
  title: "Shared/Admin/Admin frame",
  component: AdminFrame,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: "/admin/users",
    docs: {
      description: {
        component:
          "The frame of the Admin pages: one header, then Users and Agents as tabs, each at its own address. The current view's content goes inside.",
      },
    },
  },
  args: {
    children: (
      <Placeholder
        title="Accounts"
        rows={[
          "Harald Example, harald@example.org, admin",
          "Zoë Ångström, friend@example.org, member",
        ]}
      />
    ),
  },
} satisfies Meta<typeof AdminFrame>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Users is the current view. */
export const UsersView: Story = { name: "Users view" };

/** Agents is the current view. */
export const AgentsView: Story = {
  name: "Agents view",
  parameters: { at: "/admin/agents" },
  args: {
    children: (
      <Placeholder
        title="Agents"
        rows={["Posting check, claude-opus-5-5", "Chat, claude-opus-5-5"]}
      />
    ),
  },
};

/** On a phone the header and the tabs stack above the content. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
