import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { userEvent, within } from "storybook/test";

import { TermChips } from "@/components/shared/profile/term-chips";
import { Card, CardContent } from "@/components/ui/card";

interface ListForm {
  list: { advanced_regex: boolean; terms: string[] };
}

/** A term list in a form of its own, as each form that holds one gives it. */
function Harness({
  terms = [],
  advanced = false,
  regexSwitch = true,
  serverError,
  label = "streaming",
  description,
}: {
  terms?: string[];
  advanced?: boolean;
  regexSwitch?: boolean;
  /** what the server said of the first term on a save */
  serverError?: string;
  label?: string;
  description?: string;
}) {
  const form = useForm<ListForm>({
    defaultValues: { list: { advanced_regex: advanced, terms } },
  });
  useEffect(() => {
    if (serverError)
      form.setError("list.terms.0", { type: "server", message: serverError });
  }, [form, serverError]);
  return (
    <Card>
      <CardContent>
        <TermChips
          form={form}
          name="list"
          label={label}
          title="Terms"
          description={description}
          regexSwitch={regexSwitch}
        />
      </CardContent>
    </Card>
  );
}

const meta = {
  title: "Shared/Profile/Term chips",
  component: Harness,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A list of terms as chips, with the field that adds one, a remove button per chip and, for skills, the switch to regular expressions: the categories and requirements of Advanced matching on the profile page, the lists of Settings, Search and Find jobs, the industries of the search.",
      },
    },
  },
  args: { terms: ["kafka", "flink", "spark streaming"] },
} satisfies Meta<typeof Harness>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Words: a posting matches one when it names it, whole words, any case. */
export const Words: Story = {
  name: "A list of words",
};

/** Nothing in the list yet: the field to add the first term. */
export const Empty: Story = {
  name: "No term yet",
  args: { terms: [] },
};

/** Regular expressions: the switch is on and the chips are set in monospace. */
export const RegularExpressions: Story = {
  name: "Regular expressions",
  args: {
    label: "llm",
    advanced: true,
    terms: ["\\bllms?\\b", "large language models?", "gpt-?\\d"],
  },
};

/** A term the browser refuses, said under the field before anything is saved. */
export const RefusedTerm: Story = {
  name: "A term refused as it is added",
  args: { label: "llm", advanced: true, terms: ["\\bllms?\\b"] },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      canvas.getByRole("textbox", { name: "New term for llm" }),
      "(a+)+{Enter}",
    );
  },
};

/** A term the server refused on a save: its sentence shows under the chip. */
export const ServerRefusal: Story = {
  name: "A term the server refused",
  args: {
    label: "llm",
    advanced: true,
    terms: ["(?<n>a)", "\\bllms?\\b"],
    serverError: "Invalid regular expression at position 2: unknown extension.",
  },
};

/** A list an earlier version saved as regular expressions, where only words are typed now. */
export const SavedAsPatterns: Story = {
  name: "A list saved as patterns, without the switch",
  args: {
    label: "Unwanted places",
    regexSwitch: false,
    advanced: true,
    terms: ["\\bUSA?\\b", "united states"],
  },
};

/** Long terms are cut on their chip; the tooltip shows them whole. */
export const LongTerms: Story = {
  name: "Long terms",
  args: {
    label: "Job titles",
    regexSwitch: false,
    description: "A posting is kept when its title has one of these.",
    terms: [
      "senior data platform engineer for real time logistics and freight analytics",
      "analytics engineer",
      "staff data engineer, streaming and batch pipelines across three continents",
    ],
  },
};

/** On a phone the chips wrap and the field keeps its Add button. */
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
