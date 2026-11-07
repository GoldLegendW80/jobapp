/** accent: api/app/documents/templates/accent, a short accent bar over the header. */
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

export function AccentCv({ page }: LayoutProps) {
  return (
    <>
      <div className="bar" />
      <BaseHeader page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} wrap="schools" />
      <SkillsBlock page={page} />
      <LanguagesBlock page={page} />
    </>
  );
}

export function AccentLetter({ page }: LayoutProps) {
  return (
    <>
      <div className="bar" />
      <BaseHeader page={page} />
      <LetterBody page={page} />
    </>
  );
}
