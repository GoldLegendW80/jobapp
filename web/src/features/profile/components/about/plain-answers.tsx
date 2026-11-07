import { memo, useState } from "react";

import { Accordion } from "@/components/ui/accordion";
import { Form } from "@/components/ui/form";
import {
  ConflictDialog,
  FormErrorAlert,
  SaveBar,
} from "@/components/shared/profile/section-ui";

import type { Profile } from "../../api/profile-api";
import { useSectionForm } from "../../api/use-section-form";
import {
  answersSchema,
  fromAnswersForm,
  toAnswersForm,
} from "@/features/profile/helpers/forms";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";
import { Availability } from "./answers/availability";
import { GROUP_FIELDS } from "./answers/choices";
import { EqualOpportunity } from "./answers/equal-opportunity";
import { LeaveBlank } from "./answers/leave-blank";
import { OnForms } from "./answers/on-forms";
import { OwnWords } from "./answers/own-words";
import { Salary } from "./answers/salary";
import { WorkAuthorization } from "./answers/work-authorization";
import { WorkSetup } from "./answers/work-setup";
import { PlainCard } from "./plain-card";

/**
 * The answers by hand, while no model key is saved (owner, 2026-11-07): what the apply agent
 * writes when a form asks something the CV does not say, saved as a revision of the answers
 * section like the former Answers tab.
 */
export const PlainAnswers = memo(function PlainAnswers({
  profile,
  onDirtyChange,
  onSaved,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => void;
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
      onSaved,
    });
  const { control } = form;
  const [open, setOpen] = useState<string[]>([]);
  // A group with a refused field stays open, so its message shows.
  const errors = form.formState.errors;
  const refused = Object.entries(GROUP_FIELDS)
    .filter(([, fields]) => fields.some((field) => field in errors))
    .map(([group]) => group);

  return (
    <div className="flex flex-col gap-4">
      <Form {...form}>
        <form
          noValidate
          aria-label="Answers"
          className="flex flex-col gap-4"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <PlainCard
            anchor="about-answers"
            title="Answers"
            description={W.plainAnswers}
          >
            <Accordion
              type="multiple"
              value={[...new Set([...open, ...refused])]}
              onValueChange={setOpen}
            >
              <WorkAuthorization control={control} />
              <Availability control={control} />
              <OnForms control={control} />
              <WorkSetup control={control} />
              <Salary control={control} />
              <EqualOpportunity control={control} />
              <OwnWords control={control} />
              <LeaveBlank control={control} />
            </Accordion>
          </PlainCard>
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
});
