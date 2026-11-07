/** serif: api/app/documents/templates/serif, a book serif, centred headings between hairlines. */
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

export function SerifCv({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} wrap="schools" />
      <SkillsBlock page={page} section="block skills" />
      <LanguagesBlock page={page} />
    </>
  );
}

export function SerifLetter({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <LetterBody page={page} />
    </>
  );
}
