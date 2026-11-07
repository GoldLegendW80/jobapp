import { memo, useState } from "react";
import type { Control } from "react-hook-form";

import { Accordion } from "@/components/ui/accordion";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from "@/components/ui/input-group";
import {
  ConflictDialog,
  FormErrorAlert,
  SaveBar,
} from "@/components/shared/profile/section-ui";

import type { Profile } from "../../api/profile-api";
import { useSectionForm } from "../../api/use-section-form";
import {
  fromSkillsForm,
  SKILL_LISTS,
  skillsSchema,
  toWordsForm,
  withWords,
  type SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";
import { RescoreLine } from "../skills-rescore";
import { CategoryLists } from "../skills/category-list";
import { Requirements } from "../skills/requirements-group";
import { AdvancedLine } from "./advanced-line";
import { PlainCard } from "./plain-card";

/**
 * The skills by hand, while no model key is saved (owner, 2026-11-07): the years, the categories
 * and the requirements written in words, saved as a revision of the skills section like the
 * former Skills tab. The regular expressions stay Advanced matching's and are kept as stored.
 */
export const PlainSkills = memo(function PlainSkills({
  profile,
  advanced,
  onDirtyChange,
  onSaved,
}: {
  profile: Profile;
  /** the regular expression categories and requirements, which Advanced matching edits */
  advanced: number;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => void;
}) {
  // The revision of the last save here: the rescore the server runs after it is followed.
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const stored = profile.sections.skills;
  const toSkills = (values: SkillsForm) =>
    withWords(stored, fromSkillsForm(values));
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "skills",
      profile,
      schema: skillsSchema,
      toForm: toWordsForm,
      fromForm: toSkills,
      rules: { unique: { gates: "name" } },
      onDirtyChange,
      onSaved: (saved) => {
        setSavedAt(saved.revision);
        onSaved();
      },
    });
  const dirty = form.formState.isDirty;
  const [open, setOpen] = useState<string[]>([]);
  // A list with a refused field stays open, so its message shows.
  const errors = form.formState.errors;
  const refused = FOLDED.filter((list) => list in errors);

  return (
    <div className="flex flex-col gap-4">
      <Form {...form}>
        <form
          noValidate
          aria-label="Skills"
          className="flex flex-col gap-4"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <PlainCard
            anchor="about-skills"
            title="Skills"
            description={W.plainSkills}
          >
            <YearsField control={form.control} />
            <Accordion
              type="multiple"
              value={[...new Set([...open, ...refused])]}
              onValueChange={setOpen}
            >
              <CategoryLists form={form} mode="words" />
              <Requirements form={form} mode="words" />
            </Accordion>
            {advanced > 0 ? <AdvancedLine count={advanced} /> : null}
          </PlainCard>
          <SaveBar
            label="Skills"
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
      <ConflictDialog
        open={conflict}
        label="Skills"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
});

/** The lists folded in the Accordion, by their values there. */
const FOLDED = [...SKILL_LISTS, "gates"] as const;

function YearsField({ control }: { control: Control<SkillsForm> }) {
  return (
    <FormField
      control={control}
      name="years_experience"
      render={({ field }) => (
        <FormItem className="max-w-56">
          <FormLabel>{W.years}</FormLabel>
          <InputGroup>
            <FormControl>
              <InputGroupInput
                inputMode="decimal"
                autoComplete="off"
                placeholder="3.5"
                {...field}
              />
            </FormControl>
            <InputGroupAddon align="inline-end">
              <InputGroupText>{W.yearsUnit}</InputGroupText>
            </InputGroupAddon>
          </InputGroup>
          <FormDescription>{W.yearsHelp}</FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
