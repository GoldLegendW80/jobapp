/** bold: api/app/documents/templates/bold, a very large name, headings on black bars. */
import { codePoints } from "@/features/documents/helpers/sheet-page";
import {
  ContactLines,
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
  const size = codePoints(page.name);
  return (
    <header className="head">
      <Lines>
        <div className="headtext">
          <Lines>
            <Name
              page={page}
              words="w"
              className={
                size > 60 ? "name longer" : size > 30 ? "name long" : "name"
              }
            />
            <Headline page={page} />
            <ContactLines page={page} />
          </Lines>
        </div>
        <Portrait page={page} />
      </Lines>
    </header>
  );
}

export function BoldCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
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

export function BoldLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} words="w" />
    </>
  );
}
