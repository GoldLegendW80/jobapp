/** compact: api/app/documents/templates/compact, small type and a tight rhythm. */
import {
  BaseHeader,
  ExperienceBlock,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  SchoolsBlock,
  SkillsBlock,
  SummaryBlock,
  type LayoutProps,
} from "../parts";

export function CompactCv({ page }: LayoutProps) {
  return (
    <>
      <BaseHeader page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} wrap="schools" />
      <SkillsBlock page={page} wrap="skills" />
      <LanguagesBlock page={page} />
    </>
  );
}

export function CompactLetter({ page }: LayoutProps) {
  return (
    <>
      <BaseHeader page={page} />
      <LetterBody page={page} />
    </>
  );
}
