import { Plus } from "lucide-react";
import { type Control, useFieldArray } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormDescription,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";

import { EEO_TOPICS, type Profile } from "./api";
import {
  type AnswersForm,
  answersSchema,
  AUTHORIZATIONS_MAX,
  fromAnswersForm,
  LEAVE_BLANK_MAX,
  SALARY_RANGES_MAX,
  toAnswersForm,
} from "./forms";
import {
  AreaField,
  ConflictDialog,
  FormErrorAlert,
  Group,
  ItemControls,
  LabelText,
  SaveBar,
  TextField,
} from "./section-ui";
import { useSectionForm } from "./use-section-form";

const TOPIC_LABELS: Record<(typeof EEO_TOPICS)[number], string> = {
  gender: "Gender",
  ethnicity: "Ethnicity",
  race: "Race",
  disability: "Disability",
  veteran: "Veteran status",
  sexual_orientation: "Sexual orientation",
  age: "Age or date of birth",
};

/** A native select over a short list of values, with its label, description and error. */
function ChoiceField({
  control,
  name,
  label,
  srSuffix,
  description,
  options,
  className,
}: {
  control: Control<AnswersForm>;
  name:
    | "eeo_policy"
    | "salary_stance"
    | `eeo_answers.${number}.topic`
    | `salary_ranges.${number}.period`
    | "willing_onsite"
    | "willing_hybrid"
    | "previously_applied_default"
    | "background_check_consent"
    | "reference_check_consent";
  label: string;
  srSuffix?: string;
  description?: string;
  options: readonly { value: string; label: string }[];
  className?: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className={className}>
          <FormLabel>
            <LabelText label={label} srSuffix={srSuffix} />
          </FormLabel>
          <FormControl>
            <NativeSelect {...field}>
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </NativeSelect>
          </FormControl>
          {description ? (
            <FormDescription>{description}</FormDescription>
          ) : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

const YES_NO_NOT_SAID = [
  { value: "", label: "Not said" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
] as const;

export function AnswersTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "answers",
      profile,
      schema: answersSchema,
      toForm: toAnswersForm,
      fromForm: fromAnswersForm,
      rules: {
        unique: {
          work_authorization: "country",
          eeo_answers: "topic",
          salary_ranges: "currency",
        },
        wrapped: ["leave_blank"],
      },
      onDirtyChange,
    });
  const countries = useFieldArray({
    control: form.control,
    name: "work_authorization",
  });
  const blanks = useFieldArray({ control: form.control, name: "leave_blank" });
  const eeoAnswers = useFieldArray({
    control: form.control,
    name: "eeo_answers",
  });
  const ranges = useFieldArray({
    control: form.control,
    name: "salary_ranges",
  });
  const { control } = form;
  const eeoPolicy = form.watch("eeo_policy");
  const usedTopics = eeoAnswers.fields.map((f) => f.topic);
  const nextTopic = EEO_TOPICS.find((t) => !usedTopics.includes(t));

  return (
    <div className="flex flex-col gap-6">
      <Form {...form}>
        <form
          noValidate
          aria-label="Answers"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="Work authorisation"
            description="What to answer per country or region when a form asks whether you may work there."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={countries.fields.length >= AUTHORIZATIONS_MAX}
                onClick={() => {
                  countries.append(
                    { country: "", answer: "", needs_sponsorship: "" },
                    { shouldFocus: true },
                  );
                }}
              >
                <Plus aria-hidden="true" />
                Add country
              </Button>
            }
          >
            {countries.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">No country yet.</p>
            ) : (
              <ol className="flex flex-col gap-4">
                {countries.fields.map((item, index) => {
                  const n = index + 1;
                  return (
                    <li key={item.id} className="rounded-md border p-3 sm:p-4">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <h4 className="text-sm font-medium">Country {n}</h4>
                        <ItemControls
                          index={index}
                          count={countries.fields.length}
                          name={`country ${n}`}
                          onMove={countries.move}
                          onRemove={countries.remove}
                        />
                      </div>
                      <div className="grid gap-4 sm:grid-cols-2">
                        <TextField
                          control={control}
                          name={`work_authorization.${index}.country`}
                          label="Country or region"
                          srSuffix={`${n}`}
                        />
                        <FormField
                          control={control}
                          name={`work_authorization.${index}.needs_sponsorship`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                <LabelText
                                  label="Needs visa sponsorship"
                                  srSuffix={`in country ${n}`}
                                />
                              </FormLabel>
                              <FormControl>
                                <NativeSelect {...field}>
                                  <option value="">Not specified</option>
                                  <option value="no">No</option>
                                  <option value="yes">Yes</option>
                                </NativeSelect>
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <AreaField
                          control={control}
                          name={`work_authorization.${index}.answer`}
                          label="Answer"
                          srSuffix={`for country ${n}`}
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

          <Group title="Availability and conditions">
            <div className="grid gap-4 sm:grid-cols-2">
              <AreaField
                control={control}
                name="notice_period"
                label="Notice period"
                rows={2}
              />
              <TextField
                control={control}
                name="earliest_start"
                label="Earliest start"
                placeholder="December 2026"
              />
              <AreaField
                control={control}
                name="salary_policy"
                label="Salary policy"
                description="What to write when a form asks for an expected salary."
                rows={2}
                className="sm:col-span-2"
              />
              <AreaField
                control={control}
                name="relocation"
                label="Relocation and remote work"
                rows={3}
              />
              <AreaField
                control={control}
                name="travel"
                label="Travel"
                rows={3}
              />
            </div>
          </Group>

          <Group
            title="About you on forms"
            description="Optional. Used when a form asks for these; left empty, the field is left to you."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                control={control}
                name="preferred_name"
                label="Preferred name"
                description="Only for fields that ask what to call you."
              />
              <TextField control={control} name="pronouns" label="Pronouns" />
              <TextField
                control={control}
                name="years_experience_stated"
                label="Years of experience, as you state them"
                placeholder="About 3.5 years"
                className="sm:col-span-2"
              />
              <TextField
                control={control}
                name="highest_degree"
                label="Highest degree"
              />
              <TextField
                control={control}
                name="highest_degree_year"
                label="Year of the highest degree"
                inputMode="numeric"
                placeholder="2022"
              />
            </div>
          </Group>

          <Group
            title="Work setup and consents"
            description="Your yes or no for questions forms ask. Not said means the field is left to you. Consents are never assumed."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <ChoiceField
                control={control}
                name="willing_onsite"
                label="Willing to work on site"
                options={YES_NO_NOT_SAID}
              />
              <ChoiceField
                control={control}
                name="willing_hybrid"
                label="Willing to work hybrid"
                options={YES_NO_NOT_SAID}
              />
              <ChoiceField
                control={control}
                name="previously_applied_default"
                label="Previously applied or worked at a company"
                description="The default answer. Your own applications in this app are checked first."
                options={YES_NO_NOT_SAID}
              />
              <ChoiceField
                control={control}
                name="background_check_consent"
                label="Consent to a background check"
                options={YES_NO_NOT_SAID}
              />
              <ChoiceField
                control={control}
                name="reference_check_consent"
                label="Consent to a reference check"
                options={YES_NO_NOT_SAID}
              />
            </div>
          </Group>

          <Group
            title="Salary"
            description="What forms get when they ask for a salary. Never a figure you did not write."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={ranges.fields.length >= SALARY_RANGES_MAX}
                onClick={() => {
                  ranges.append(
                    {
                      currency: "",
                      minimum: "",
                      maximum: "",
                      period: "year",
                    },
                    { shouldFocus: true },
                  );
                }}
              >
                <Plus aria-hidden="true" />
                Add range
              </Button>
            }
          >
            <ChoiceField
              control={control}
              name="salary_stance"
              label="Salary stance"
              description="Automatic uses the salary policy sentence when it is filled and leaves the field blank otherwise."
              options={[
                { value: "", label: "Automatic" },
                { value: "leave_blank", label: "Leave it blank" },
                { value: "range", label: "Give my range" },
                { value: "sentence", label: "Write my salary policy" },
              ]}
            />
            {ranges.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">No range yet.</p>
            ) : (
              <ol className="flex flex-col gap-4">
                {ranges.fields.map((item, index) => {
                  const n = index + 1;
                  return (
                    <li key={item.id} className="rounded-md border p-3 sm:p-4">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <h4 className="text-sm font-medium">Range {n}</h4>
                        <ItemControls
                          index={index}
                          count={ranges.fields.length}
                          name={`range ${n}`}
                          onMove={ranges.move}
                          onRemove={ranges.remove}
                        />
                      </div>
                      <div className="grid gap-4 sm:grid-cols-2">
                        <TextField
                          control={control}
                          name={`salary_ranges.${index}.currency`}
                          label="Currency"
                          srSuffix={`of range ${n}`}
                          placeholder="EUR"
                          maxLength={3}
                        />
                        <ChoiceField
                          control={control}
                          name={`salary_ranges.${index}.period`}
                          label="Per"
                          srSuffix={`of range ${n}`}
                          options={[
                            { value: "year", label: "Year" },
                            { value: "month", label: "Month" },
                            { value: "day", label: "Day" },
                          ]}
                        />
                        <TextField
                          control={control}
                          name={`salary_ranges.${index}.minimum`}
                          label="Minimum"
                          srSuffix={`of range ${n}`}
                          inputMode="numeric"
                        />
                        <TextField
                          control={control}
                          name={`salary_ranges.${index}.maximum`}
                          label="Maximum"
                          srSuffix={`of range ${n}`}
                          inputMode="numeric"
                        />
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </Group>

          <Group
            title="Equal opportunity questions"
            description="Gender, ethnicity, disability and the like. Nothing is ever inferred."
            action={
              eeoPolicy === "stored" ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={nextTopic === undefined}
                  onClick={() => {
                    if (nextTopic !== undefined) {
                      eeoAnswers.append(
                        { topic: nextTopic, answer: "" },
                        { shouldFocus: true },
                      );
                    }
                  }}
                >
                  <Plus aria-hidden="true" />
                  Add answer
                </Button>
              ) : undefined
            }
          >
            <ChoiceField
              control={control}
              name="eeo_policy"
              label="What to do"
              description="Decline picks the 'prefer not to say' option when a form offers one and leaves the field otherwise."
              options={[
                { value: "decline", label: "Decline to answer" },
                { value: "leave", label: "Leave them to me" },
                { value: "stored", label: "Use the answers I store here" },
              ]}
            />
            {eeoPolicy === "stored" ? (
              eeoAnswers.fields.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No answer stored yet. A topic without one is left to you.
                </p>
              ) : (
                <ol className="flex flex-col gap-3">
                  {eeoAnswers.fields.map((item, index) => (
                    <li
                      key={item.id}
                      className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-start"
                    >
                      <ChoiceField
                        control={control}
                        name={`eeo_answers.${index}.topic`}
                        label="Topic"
                        srSuffix={`${index + 1}`}
                        options={EEO_TOPICS.map((t) => ({
                          value: t,
                          label: TOPIC_LABELS[t],
                        }))}
                      />
                      <TextField
                        control={control}
                        name={`eeo_answers.${index}.answer`}
                        label="Answer"
                        srSuffix={`for topic ${index + 1}`}
                      />
                      <div className="sm:pt-6">
                        <ItemControls
                          index={index}
                          count={eeoAnswers.fields.length}
                          name={`answer ${index + 1}`}
                          onMove={eeoAnswers.move}
                          onRemove={eeoAnswers.remove}
                        />
                      </div>
                    </li>
                  ))}
                </ol>
              )
            ) : null}
          </Group>

          <Group title="In your own words">
            <AreaField
              control={control}
              name="motivation"
              label="Motivation"
              description="Why you want this kind of role. Letters and answers start from it."
              rows={5}
            />
            <AreaField
              control={control}
              name="heard_about"
              label="How you heard about the role"
              rows={2}
            />
          </Group>

          <Group
            title="Leave blank"
            description="Questions forms ask that you always answer yourself, such as voluntary self identification."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={blanks.fields.length >= LEAVE_BLANK_MAX}
                onClick={() => {
                  blanks.append({ value: "" }, { shouldFocus: true });
                }}
              >
                <Plus aria-hidden="true" />
                Add question
              </Button>
            }
          >
            {blanks.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing listed.</p>
            ) : (
              <ol className="flex flex-col gap-3">
                {blanks.fields.map((item, index) => (
                  <li
                    key={item.id}
                    className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-start"
                  >
                    <TextField
                      control={control}
                      name={`leave_blank.${index}.value`}
                      label="Question"
                      srSuffix={`${index + 1} to leave blank`}
                    />
                    <div className="sm:pt-6">
                      <ItemControls
                        index={index}
                        count={blanks.fields.length}
                        name={`question ${index + 1}`}
                        onMove={blanks.move}
                        onRemove={blanks.remove}
                      />
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Group>
          <SaveBar
            label="Answers"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <ConflictDialog
        open={conflict}
        label="Answers"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
}
