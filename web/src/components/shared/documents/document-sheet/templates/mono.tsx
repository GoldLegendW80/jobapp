/** mono: api/app/documents/templates/mono, a monospace face for engineering profiles. */
import {
  ExperienceBlock,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  SchoolsBlock,
  SkillsBlock,
  StackedHeader,
  SummaryBlock,
  type LayoutProps,
} from "../parts";

export function MonoCv({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} wrap="schools" />
      <SkillsBlock page={page} wrap="code" />
      <LanguagesBlock page={page} />
    </>
  );
}

export function MonoLetter({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <LetterBody page={page} />
    </>
  );
}
