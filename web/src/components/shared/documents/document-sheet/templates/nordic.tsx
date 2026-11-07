/** nordic: api/app/documents/templates/nordic, wide margins in a muted palette. */
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

export function NordicCv({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} />
      <SkillsBlock page={page} section="block skills" />
      <LanguagesBlock page={page} />
    </>
  );
}

export function NordicLetter({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <LetterBody page={page} />
    </>
  );
}
