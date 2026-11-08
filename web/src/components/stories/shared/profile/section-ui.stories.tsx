import type { Meta, StoryObj } from "@storybook/react-vite";
import { Plus } from "lucide-react";
import { useEffect } from "react";
import { useFieldArray, useForm } from "react-hook-form";

import {
  AreaField,
  ConflictDialog,
  FormErrorAlert,
  Group,
  ITEM,
  ITEM_LIST,
  ItemControls,
  PeriodField,
  SaveBar,
  TextField,
} from "@/components/shared/profile/section-ui";
import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";

interface SchoolsForm {
  headline: string;
  summary: string;
  schools: { school: string; start: string; end: string }[];
}

const SCHOOLS: SchoolsForm = {
  headline: "Senior data engineer",
  summary: "Seven years of data pipelines, the last three on streaming.",
  schools: [
    {
      school: "Institut Polytechnique de Fourvière",
      start: "2014",
      end: "2019-06",
    },
    { school: "Lycée du Parc Exemple", start: "2011-09", end: "2014-06" },
  ],
};

/** A section's form as the profile page builds one: groups of fields, then the save bar. */
function SectionForm({
  values = SCHOOLS,
  error,
  fieldError,
  dirty = false,
  saving = false,
  note,
}: {
  values?: SchoolsForm;
  error?: string;
  fieldError?: string;
  dirty?: boolean;
  saving?: boolean;
  note?: string;
}) {
  const form = useForm<SchoolsForm>({ defaultValues: values });
  const schools = useFieldArray({ control: form.control, name: "schools" });
  useEffect(() => {
    if (dirty)
      form.setValue("headline", `${values.headline}, streaming`, {
        shouldDirty: true,
      });
    if (fieldError)
      form.setError("schools.1.end", { type: "server", message: fieldError });
  }, [dirty, fieldError, form, values.headline]);
  return (
    <Form {...form}>
      <form
        noValidate
        aria-label="Profile section"
        className="flex flex-col gap-6"
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <FormErrorAlert message={error} />
        <Group
          title="You"
          description="Your name and contact details, as every document shows them."
        >
          <TextField control={form.control} name="headline" label="Headline" />
          <AreaField
            control={form.control}
            name="summary"
            label="Summary"
            description="One or two sentences, as the top of a CV."
          />
        </Group>
        <Group
          title="Education"
          description="Schools and degrees, most recent first. A year alone is enough."
          action={
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                schools.append({ school: "", start: "", end: "" });
              }}
            >
              <Plus aria-hidden="true" />
              Add school
            </Button>
          }
        >
          {schools.fields.length === 0 ? (
            <p className="text-sm text-muted-foreground">No school yet.</p>
          ) : (
            <ol className={ITEM_LIST}>
              {schools.fields.map((item, index) => (
                <li key={item.id} className={ITEM}>
                  <div className="flex items-end justify-between gap-2">
                    <TextField
                      control={form.control}
                      name={`schools.${index}.school`}
                      label="School"
                      srSuffix={String(index + 1)}
                      className="min-w-0 flex-1"
                    />
                    <ItemControls
                      index={index}
                      count={schools.fields.length}
                      name={`school ${String(index + 1)}`}
                      onMove={schools.move}
                      onRemove={schools.remove}
                    />
                  </div>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <PeriodField
                      control={form.control}
                      name={`schools.${index}.start`}
                      label="Start"
                      srSuffix={`of school ${String(index + 1)}`}
                    />
                    <PeriodField
                      control={form.control}
                      name={`schools.${index}.end`}
                      label="End"
                      srSuffix={`of school ${String(index + 1)}`}
                    />
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Group>
        <SaveBar label="Experience" saving={saving} dirty={dirty} note={note} />
      </form>
    </Form>
  );
}

const meta = {
  title: "Shared/Profile/Section form parts",
  component: SectionForm,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The parts every form of the profile is built from: a titled group of fields on a card, text, multi line and date fields bound to the form, the move and remove buttons of an item, the error above the form, the save bar and the dialog of a save made elsewhere. The profile page's Experience and Contact forms, Settings, Search and the first login wizard use them.",
      },
    },
  },
} satisfies Meta<typeof SectionForm>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Saved: the bar holds Save alone, and says "All changes saved" to screen readers only. */
export const Saved: Story = {
  name: "Everything saved",
};

/** An edit not saved yet: the bar says so beside Save. */
export const Unsaved: Story = {
  name: "Unsaved changes",
  args: { dirty: true },
};

/** Save was clicked: the button waits for the server. */
export const Saving: Story = {
  name: "Saving",
  args: { dirty: true, saving: true },
};

/** A line beside Save on what the last save started (the rescore of postings after a skills change). */
export const WithANote: Story = {
  name: "Save with a note beside it",
  args: {
    dirty: false,
    note: "4 more postings now fit you at 70 or more.",
  },
};

/** The server refused a field it names: the message under that field. */
export const FieldRefused: Story = {
  name: "A field the server refused",
  args: { fieldError: "The end is before the start." },
};

/** The server refused the section without naming a field: the sentence above the form. */
export const SectionRefused: Story = {
  name: "A save refused above the form",
  args: {
    error:
      "Experience was not saved. The server could not be reached. Try again.",
  },
};

/** An empty list says so, with the button that adds its first item. */
export const EmptyList: Story = {
  name: "An empty list",
  args: { values: { headline: "", summary: "", schools: [] } },
};

/** On a phone the fields stack and the save bar spans the width. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { dirty: true },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};

/** A save answered that another tab or device saved the section first. */
export const ChangedElsewhere: Story = {
  name: "Changed in another tab",
  render: () => (
    <ConflictDialog
      open
      label="Experience"
      onKeepEditing={() => undefined}
      onReload={() => undefined}
    />
  ),
};
