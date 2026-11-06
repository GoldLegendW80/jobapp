/**
 * Skills: the scoring model. Years of experience; three lists of categories (have, adjacent,
 * lacking), each a name and term chips with its own advanced switch; gates with a penalty, shown
 * as "Requirements you do not meet" with the "Points taken off" a match costs the fit; and a
 * "Try it" box that matches a pasted posting against the categories and gates being edited,
 * with the server's matching rules ported to the browser (matching.ts).
 */
import { useState } from "react";

import { Form } from "@/components/ui/form";

import type { Profile } from "../api/profile-api";
import { useSectionForm } from "../api/use-section-form";
import {
  fromSkillsForm,
  SKILL_LISTS,
  skillsSchema,
  toSkillsForm,
} from "../helpers/editor-forms";
import {
  ConflictDialog,
  FormErrorAlert,
  Group,
  SaveBar,
  TextField,
} from "@/components/shared/profile/section-ui";
import { CategoryList } from "./skills/category-list";
import { RequirementsGroup } from "./skills/requirements-group";
import { TryIt } from "./skills/try-it";
import { RescoreLine, SkillsCostLine } from "./skills-rescore";

export function SkillsTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  // The revision of the last save here: the rescore the server runs after it is followed.
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "skills",
      profile,
      schema: skillsSchema,
      toForm: toSkillsForm,
      fromForm: fromSkillsForm,
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
          aria-label="Skills"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="Experience"
            description="Postings that ask for more years than this lose points."
          >
            <TextField
              control={form.control}
              name="years_experience"
              label="Years of experience"
              inputMode="decimal"
              autoComplete="off"
              placeholder="3.5"
              className="max-w-48"
            />
          </Group>
          {SKILL_LISTS.map((list) => (
            <CategoryList key={list} form={form} list={list} />
          ))}
          <RequirementsGroup form={form} />
          <SaveBar
            label="Skills"
            saving={saving}
            dirty={dirty}
            note={
              dirty ? (
                <SkillsCostLine control={form.control} dirty={dirty} />
              ) : savedAt !== null ? (
                <RescoreLine revision={savedAt} />
              ) : null
            }
          />
        </form>
      </Form>
      <TryIt form={form} />
      <ConflictDialog
        open={conflict}
        label="Skills"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
}
