import {
  MAX,
  type EmployerFact,
} from "@/features/documents/helpers/edit-content";
import type { Form } from "@/features/documents/helpers/edit-form";
import { FIXED_LISTS } from "@/features/documents/helpers/fixed-facts";
import { ListField } from "../edit-fields";
import { ExperienceSection } from "./experience-section";
import { FixedList } from "./fixed-list";
import { FormLines, type FormLine } from "./form-lines";
import { FormSection } from "./form-section";
import { SkillsSection } from "./skills-section";

const CV_TOP: FormLine[] = [
  ["target", "What this CV is for (not printed)", MAX.line],
  ["headline", "Headline", MAX.headline],
  ["summary", "Summary", MAX.summary, true],
];

/** The Edit view of a CV; a base CV also has its own fixed facts. */
export function CvForm({
  form,
  base,
  employers,
  known,
}: {
  form: Form;
  base: boolean;
  employers: Map<string, EmployerFact>;
  known: boolean;
}) {
  return (
    <>
      <FormSection title="Top of the page">
        <FormLines form={form} lines={CV_TOP} />
        <ListField
          form={form}
          path={["highlights"]}
          legend="Highlights"
          noun="Highlight"
          max={MAX.highlights}
          itemMax={MAX.bullet}
        />
        <ListField
          form={form}
          path={["competencies"]}
          legend="Competencies"
          noun="Competency"
          max={MAX.competencies}
          itemMax={MAX.term}
        />
      </FormSection>
      <ExperienceSection form={form} employers={employers} known={known} />
      <SkillsSection form={form} />
      {base ? (
        <FormSection title="Facts of this base CV">
          <p className="text-sm text-muted-foreground">
            Used only where your profile is still empty: your profile always
            comes first.
          </p>
          {FIXED_LISTS.map((list) => (
            <FixedList key={list.name} form={form} list={list} />
          ))}
        </FormSection>
      ) : null}
      <FormSection title="For you">
        <FormLines
          form={form}
          lines={[["notes", "Notes (not printed)", MAX.notes, true]]}
        />
      </FormSection>
    </>
  );
}
