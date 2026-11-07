/** elegant: api/app/documents/templates/elegant, a double frame and a display serif. */
import type { SheetSchool } from "@/features/documents/helpers/sheet-page";
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
      <div className="frame" />
      <header className="head">
        <Lines>
          <div className="headtext">
            <Lines>
              <Name page={page} words="w" />
              <Headline page={page} />
            </Lines>
          </div>
          <ContactLines page={page} />
        </Lines>
      </header>
    </>
  );
}

function School({ item }: { item: SheetSchool }) {
  return (
    <div className="school">
      <Lines>
        <div className="entry-head">
          <Lines>
            <h3 aria-level={4} className="entry-title">
              {item.degree}
              {item.degree && item.school ? ", " : ""}
              <span className="org">{item.school}</span>
            </h3>
            {item.period ? <p className="period">{item.period}</p> : null}
          </Lines>
        </div>
        {item.location ? <p className="place">{item.location}</p> : null}
      </Lines>
    </div>
  );
}

export function ElegantCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} school={(item) => <School item={item} />} />
      <SkillsBlock page={page} />
      <LanguagesBlock page={page} />
    </>
  );
}

export function ElegantLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} words="w" />
    </>
  );
}
