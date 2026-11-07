/** executive: api/app/documents/templates/executive, two tones under a double rule. */
import {
  ContactLines,
  ExperienceBlock,
  Headline,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  Name,
  SchoolsBlock,
  SkillsBlock,
  SummaryBlock,
  type LayoutProps,
  Lines,
} from "../parts";

function Head({ page }: LayoutProps) {
  return (
    <>
      <header className="head">
        <Lines>
          <div className="headtext">
            <Lines>
              <Name page={page} />
              <Headline page={page} />
            </Lines>
          </div>
          <ContactLines page={page} />
        </Lines>
      </header>
      <div className="rules" />
    </>
  );
}

export function ExecutiveCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} />
      <SkillsBlock page={page} />
      <LanguagesBlock page={page} />
    </>
  );
}

export function ExecutiveLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} />
    </>
  );
}
