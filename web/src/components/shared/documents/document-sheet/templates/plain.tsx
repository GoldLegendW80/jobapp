/** plain: api/app/documents/templates/plain, strict ats in black on white. */
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

export function PlainCv({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" list={null} />
      <ListBlock page={page} part="competencies" joined />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} />
      <SkillsBlock page={page} />
      <LanguagesBlock page={page} />
    </>
  );
}

export function PlainLetter({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <LetterBody page={page} />
    </>
  );
}
