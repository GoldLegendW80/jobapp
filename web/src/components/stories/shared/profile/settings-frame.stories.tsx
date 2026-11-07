import type { Meta, StoryObj } from "@storybook/react-vite";
import { History } from "lucide-react";

import { PageBody } from "@/components/blocks/page";
import {
  SettingsFrame,
  SettingsSection,
} from "@/components/shared/profile/settings-frame";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { withApp } from "@/design-system/with-app";

/** Two groups of settings, as Settings, Search lays them out. */
function Groups() {
  return (
    <PageBody>
      <SettingsSection
        title="Documents"
        description="When the app writes a CV and a letter by itself."
      >
        <Field orientation="horizontal" className="justify-between">
          <FieldLabel htmlFor="auto-documents">
            Write documents for new postings
          </FieldLabel>
          <Switch id="auto-documents" defaultChecked />
        </Field>
        <Field className="gap-1.5">
          <FieldLabel htmlFor="min-fit">Lowest fit</FieldLabel>
          <Input id="min-fit" defaultValue="75" inputMode="numeric" />
          <FieldDescription>
            Postings with a fit of this or more get their documents.
          </FieldDescription>
        </Field>
      </SettingsSection>
      <SettingsSection
        title="Never apply"
        description="Rules the search and the apply agent always keep."
        actions={
          <Button variant="outline" size="sm">
            Add a rule
          </Button>
        }
      >
        <p className="text-sm">No defence or weapons companies.</p>
      </SettingsSection>
    </PageBody>
  );
}

const meta = {
  title: "Shared/Profile/Settings frame",
  component: SettingsFrame,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: "/settings/search",
    docs: {
      description: {
        component:
          "The frame of Settings: the header, the Search and Applications views as tabs that change the address, then the open view's groups, each its name and purpose on the left and its controls on a card on the right. SettingsSection also lays out the groups of the Account page.",
      },
    },
  },
  args: { children: <Groups /> },
} satisfies Meta<typeof SettingsFrame>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Settings, Search: the tabs, then its groups. */
export const SearchSettings: Story = {
  name: "Search settings",
};

/** The open view's own actions in the header, History on Search. */
export const WithAnAction: Story = {
  name: "With the view's own action",
  args: {
    actions: (
      <Button variant="outline" size="sm">
        <History aria-hidden="true" />
        History
      </Button>
    ),
  },
};

/** Settings, Applications: the second tab is the open one. */
export const ApplicationsSettings: Story = {
  name: "Applications settings",
  parameters: { at: "/settings/applications" },
};

/** On a phone the name of each group goes above its card. */
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

/** One group alone, as the Account page uses it. */
export const OneGroup: Story = {
  name: "One group of settings",
  render: () => (
    <SettingsSection
      title="Name"
      description="How the app greets you and signs your messages."
    >
      <Field className="gap-1.5">
        <FieldLabel htmlFor="account-name">Name</FieldLabel>
        <Input id="account-name" defaultValue="Camille Martin" />
      </Field>
    </SettingsSection>
  ),
};
