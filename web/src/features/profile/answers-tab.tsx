import { Plus } from "lucide-react";
import { useFieldArray } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";

import type { Profile } from "./api";
import {
  answersSchema,
  AUTHORIZATIONS_MAX,
  fromAnswersForm,
  LEAVE_BLANK_MAX,
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
        unique: { work_authorization: "country" },
        wrapped: ["leave_blank"],
      },
      onDirtyChange,
    });
  const countries = useFieldArray({
    control: form.control,
    name: "work_authorization",
  });
  const blanks = useFieldArray({ control: form.control, name: "leave_blank" });
  const { control } = form;

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
