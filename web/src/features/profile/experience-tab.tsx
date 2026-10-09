import { Plus } from "lucide-react";
import { useFieldArray, useWatch, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { EMPLOYER_CONTRACTS, type Profile } from "./api";
import {
  EDUCATION_MAX,
  EMPLOYERS_MAX,
  experienceSchema,
  fromExperienceForm,
  NEW_EMPLOYER,
  NEW_SCHOOL,
  slugify,
  toExperienceForm,
  type ExperienceForm,
} from "./forms";
import {
  AreaField,
  ConflictDialog,
  FormErrorAlert,
  Group,
  ItemControls,
  LabelText,
  MonthField,
  SaveBar,
  TextField,
} from "./section-ui";
import { useSectionForm } from "./use-section-form";

const CONTRACT_LABELS: Record<(typeof EMPLOYER_CONTRACTS)[number], string> = {
  CDI: "Permanent (CDI)",
  CDD: "Fixed term (CDD)",
  apprenticeship: "Apprenticeship",
  freelance: "Freelance",
  internship: "Internship",
  other: "Other",
};

export function ExperienceTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "experience",
      profile,
      schema: experienceSchema,
      toForm: toExperienceForm,
      fromForm: fromExperienceForm,
      rules: { unique: { employers: "key" } },
      onDirtyChange,
    });
  const employers = useFieldArray({ control: form.control, name: "employers" });
  const education = useFieldArray({ control: form.control, name: "education" });
  const { control } = form;

  return (
    <div className="flex flex-col gap-6">
      <Form {...form}>
        <form
          noValidate
          aria-label="Experience"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="Employers"
            description="Most recent first. These are locked facts: generated documents copy them exactly."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={employers.fields.length >= EMPLOYERS_MAX}
                onClick={() => {
                  employers.append({ ...NEW_EMPLOYER }, { shouldFocus: true });
                }}
              >
                <Plus aria-hidden="true" />
                Add employer
              </Button>
            }
          >
            {employers.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">No employer yet.</p>
            ) : (
              <ol className="flex flex-col gap-4">
                {employers.fields.map((item, index) => (
                  <li key={item.id} className="rounded-md border p-3 sm:p-4">
                    <EmployerFields
                      form={form}
                      index={index}
                      count={employers.fields.length}
                      onMove={employers.move}
                      onRemove={employers.remove}
                    />
                  </li>
                ))}
              </ol>
            )}
          </Group>

          <Group
            title="Education"
            description="Schools and degrees, most recent first. Months are optional."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={education.fields.length >= EDUCATION_MAX}
                onClick={() => {
                  education.append({ ...NEW_SCHOOL }, { shouldFocus: true });
                }}
              >
                <Plus aria-hidden="true" />
                Add school
              </Button>
            }
          >
            {education.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">No school yet.</p>
            ) : (
              <ol className="flex flex-col gap-4">
                {education.fields.map((item, index) => {
                  const n = index + 1;
                  return (
                    <li key={item.id} className="rounded-md border p-3 sm:p-4">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <h4 className="text-sm font-medium">School {n}</h4>
                        <ItemControls
                          index={index}
                          count={education.fields.length}
                          name={`school ${n}`}
                          onMove={education.move}
                          onRemove={education.remove}
                        />
                      </div>
                      <div className="grid gap-4 sm:grid-cols-2">
                        <TextField
                          control={control}
                          name={`education.${index}.school`}
                          label="School"
                          srSuffix={`${n}`}
                        />
                        <TextField
                          control={control}
                          name={`education.${index}.degree`}
                          label="Degree"
                          srSuffix={`at school ${n}`}
                        />
                        <MonthField
                          control={control}
                          name={`education.${index}.start`}
                          label="Start"
                          srSuffix={`of school ${n}`}
                        />
                        <MonthField
                          control={control}
                          name={`education.${index}.end`}
                          label="End"
                          srSuffix={`of school ${n}`}
                        />
                        <TextField
                          control={control}
                          name={`education.${index}.location`}
                          label="Location"
                          srSuffix={`of school ${n}`}
                        />
                        <AreaField
                          control={control}
                          name={`education.${index}.details`}
                          label="Details"
                          srSuffix={`of school ${n}`}
                          rows={2}
                          className="sm:col-span-2"
                        />
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </Group>
          <SaveBar
            label="Experience"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <ConflictDialog
        open={conflict}
        label="Experience"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
}

function EmployerFields({
  form,
  index,
  count,
  onMove,
  onRemove,
}: {
  form: UseFormReturn<ExperienceForm>;
  index: number;
  count: number;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  const { control } = form;
  const n = index + 1;
  const present = useWatch({ control, name: `employers.${index}.present` });
  const organisation = useWatch({
    control,
    name: `employers.${index}.organisation`,
  });
  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h4 className="min-w-0 truncate text-sm font-medium">
          {organisation.trim() || `Employer ${n}`}
        </h4>
        <ItemControls
          index={index}
          count={count}
          name={`employer ${n}`}
          onMove={onMove}
          onRemove={onRemove}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField
          control={control}
          name={`employers.${index}.organisation`}
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                <LabelText label="Organisation" srSuffix={`of employer ${n}`} />
              </FormLabel>
              <FormControl>
                <Input
                  {...field}
                  onChange={(event) => {
                    field.onChange(event);
                    if (form.getValues(`employers.${index}.autoKey`)) {
                      form.setValue(
                        `employers.${index}.key`,
                        slugify(event.target.value),
                        {
                          shouldDirty: true,
                          shouldValidate: form.formState.isSubmitted,
                        },
                      );
                    }
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name={`employers.${index}.key`}
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                <LabelText label="Key" srSuffix={`of employer ${n}`} />
              </FormLabel>
              <FormControl>
                <Input
                  {...field}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoComplete="off"
                  onChange={(event) => {
                    field.onChange(event);
                    form.setValue(`employers.${index}.autoKey`, false);
                  }}
                />
              </FormControl>
              <FormDescription>
                A short stable name documents use for this employer, made from
                the organisation until you change it.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <TextField
          control={control}
          name={`employers.${index}.title`}
          label="Job title"
          srSuffix={`at employer ${n}`}
        />
        <FormField
          control={control}
          name={`employers.${index}.contract`}
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                <LabelText label="Contract" srSuffix={`at employer ${n}`} />
              </FormLabel>
              <FormControl>
                <NativeSelect {...field}>
                  <option value="">Not specified</option>
                  {EMPLOYER_CONTRACTS.map((contract) => (
                    <option key={contract} value={contract}>
                      {CONTRACT_LABELS[contract]}
                    </option>
                  ))}
                </NativeSelect>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <MonthField
          control={control}
          name={`employers.${index}.start`}
          label="Start"
          srSuffix={`of employer ${n}`}
        />
        <div className="grid gap-2">
          <MonthField
            control={control}
            name={`employers.${index}.end`}
            label="End"
            srSuffix={`of employer ${n}`}
            disabled={present}
          />
          <FormField
            control={control}
            name={`employers.${index}.present`}
            render={({ field }) => (
              <FormItem className="flex items-center gap-2">
                <FormControl>
                  <input
                    type="checkbox"
                    className="size-4 accent-primary"
                    name={field.name}
                    ref={field.ref}
                    checked={field.value}
                    onBlur={field.onBlur}
                    onChange={(event) => {
                      field.onChange(event.target.checked);
                      if (event.target.checked) {
                        form.setValue(`employers.${index}.end`, "", {
                          shouldDirty: true,
                        });
                        form.clearErrors(`employers.${index}.end`);
                      }
                    }}
                  />
                </FormControl>
                <FormLabel className="font-normal">
                  <LabelText label="Present" srSuffix={`at employer ${n}`} />
                </FormLabel>
              </FormItem>
            )}
          />
        </div>
        <TextField
          control={control}
          name={`employers.${index}.location`}
          label="Location"
          srSuffix={`of employer ${n}`}
        />
      </div>
    </>
  );
}
