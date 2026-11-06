import { Form } from "@/components/ui/form";

import type { Profile } from "../api/profile-api";
import { useSectionForm } from "../api/use-section-form";
import {
  answersSchema,
  fromAnswersForm,
  toAnswersForm,
} from "../helpers/forms";
import { AboutYouGroup } from "./answers/about-you-group";
import { AvailabilityGroup } from "./answers/availability-group";
import { EqualOpportunityGroup } from "./answers/equal-opportunity-group";
import { LeaveBlankGroup } from "./answers/leave-blank-group";
import { OwnWordsGroup } from "./answers/own-words-group";
import { SalaryGroup } from "./answers/salary-group";
import { WorkAuthorizationGroup } from "./answers/work-authorization-group";
import { WorkSetupGroup } from "./answers/work-setup-group";
import {
  ConflictDialog,
  FormErrorAlert,
  SaveBar,
} from "@/components/shared/profile/section-ui";

/** Answers: what the apply agent writes when a form asks something the CV does not say. */
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
          <WorkAuthorizationGroup control={control} />
          <AvailabilityGroup control={control} />
          <AboutYouGroup control={control} />
          <WorkSetupGroup control={control} />
          <SalaryGroup control={control} />
          <EqualOpportunityGroup control={control} />
          <OwnWordsGroup control={control} />
          <LeaveBlankGroup control={control} />
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
