import { useState } from "react";

import { Form } from "@/components/ui/form";
import {
  ConflictDialog,
  FormErrorAlert,
  SaveBar,
} from "@/components/shared/profile/section-ui";

import type { Profile } from "../api/profile-api";
import { useSectionForm } from "../api/use-section-form";
import {
  fromSkillsForm,
  skillsSchema,
  toAdvancedForm,
  withAdvanced,
  type SkillsForm,
} from "../helpers/editor-forms";
import { RescoreLine } from "./skills-rescore";
import { CategoriesGroup } from "./skills/category-list";
import { RequirementsGroup } from "./skills/requirements-group";
import { TryIt } from "./skills/try-it";

/**
 * Advanced matching: the skill categories and requirements written as regular expressions, which
 * About you leaves out (it holds the ones in words), and Try a posting against all of them. Its
 * Save writes the skills section with these first and the ones in words as stored now, so a
 * reading of About you applied meanwhile is kept.
 */
export function AdvancedMatching({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  // The revision of the last save here: the rescore the server runs after it is followed.
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const stored = profile.sections.skills;
  const toSkills = (values: SkillsForm) =>
    withAdvanced(stored, fromSkillsForm(values));
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "skills",
      profile,
      schema: skillsSchema,
      toForm: toAdvancedForm,
      fromForm: toSkills,
      rules: { unique: { gates: "name" } },
      onDirtyChange,
      onSaved: (saved) => {
        setSavedAt(saved.revision);
      },
    });
  const dirty = form.formState.isDirty;

  return (
    <div className="flex flex-col gap-6">
      <Form {...form}>
        <form
          noValidate
          aria-label="Advanced matching"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <CategoriesGroup form={form} />
          <RequirementsGroup form={form} />
          <SaveBar
            label="Advanced matching"
            saving={saving}
            dirty={dirty}
            note={
              !dirty && savedAt !== null ? (
                <RescoreLine revision={savedAt} />
              ) : null
            }
          />
        </form>
      </Form>
      <TryIt form={form} stored={stored} />
      <ConflictDialog
        open={conflict}
        label="Skills"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
}
