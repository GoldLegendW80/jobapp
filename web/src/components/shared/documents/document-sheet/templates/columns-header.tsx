/** columns-header: api/app/documents/templates/columns-header, the contact lines in three columns. */
import {
  ContactCells,
  ExperienceBlock,
  Headline,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  Name,
  Portrait,
  SchoolsBlock,
  SkillsBlock,
  SummaryBlock,
  type LayoutProps,
  Lines,
} from "../parts";

function Head({ page }: LayoutProps) {
  return (
    <header className="head">
      <Lines>
        <div className="headtext">
          <Lines>
            <Name page={page} />
            <Headline page={page} />
          </Lines>
        </div>
        <Portrait page={page} />
      </Lines>
    </header>
  );
}

export function ColumnsHeaderCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <ContactCells page={page} cell={28} pair={62} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} />
      <SkillsBlock page={page} wrap="skills" />
      <LanguagesBlock page={page} />
    </>
  );
}

export function ColumnsHeaderLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <ContactCells page={page} cell={23} pair={51} />
      <LetterBody page={page} />
    </>
  );
}
