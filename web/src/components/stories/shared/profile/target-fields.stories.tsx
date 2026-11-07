import type { Meta, StoryObj } from "@storybook/react-vite";
import { useForm } from "react-hook-form";
import { userEvent, within } from "storybook/test";

import { TargetFields } from "@/components/shared/profile/target-fields";
import { Form } from "@/components/ui/form";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import { EMPTY, type Search } from "@/features/profile/api/profile-api";
import {
  toSearchForm,
  type SearchForm,
} from "@/features/profile/helpers/editor-forms";

/** The fields in a search form of their own. */
function Harness({
  search = camilleProfile().search,
  bare = false,
}: {
  search?: Search;
  bare?: boolean;
}) {
  const form = useForm<SearchForm>({ defaultValues: toSearchForm(search) });
  return (
    <Form {...form}>
      <form
        aria-label="Search"
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <TargetFields form={form} bare={bare} />
      </form>
    </Form>
  );
}

const meta = {
  title: "Shared/Profile/Target fields",
  component: Harness,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What the person aims for, the seniority and the pay sought, then under Advanced the industries and the company types, each a small bonus to the fit of a posting that names it: Settings, Search and the More options of Find jobs.",
      },
    },
  },
} satisfies Meta<typeof Harness>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Camille's aims: senior, a yearly salary range; Advanced folded. */
export const Aims: Story = {
  name: "Seniority and pay",
};

/** Advanced opened: the industries as words and the company types. */
export const Advanced: Story = {
  name: "Industries and company types",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Advanced" }),
    );
  },
};

/** Nothing chosen yet. */
export const Empty: Story = {
  name: "Nothing chosen yet",
  args: { search: structuredClone(EMPTY.search) },
};

/** Without its card, for a page that gives the fields a section of its own (Settings, Search). */
export const Bare: Story = {
  name: "Without its card",
  args: { bare: true },
};

/** On a phone the toggles and the pay fields wrap. */
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
