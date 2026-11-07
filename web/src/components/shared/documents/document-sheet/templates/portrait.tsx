/** portrait: api/app/documents/templates/portrait, a large photo (or the initials) beside the name. */
import { initialsOf } from "@/features/documents/helpers/sheet-page";
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
  const initials = initialsOf(page.name);
  return (
    <header
      className={page.photo === null && initials === "" ? "head bare" : "head"}
    >
      <Lines>
        <div className="headtext">
          <Lines>
            <Name page={page} />
            <Headline page={page} />
            <ContactLines page={page} />
          </Lines>
        </div>
        {page.photo !== null ? (
          <div className="frame">
            <Lines>
              <Portrait page={page} />
            </Lines>
          </div>
        ) : initials !== "" ? (
          <div className="frame initials">{initials}</div>
        ) : null}
      </Lines>
    </header>
  );
}

export function PortraitCv({ page }: LayoutProps) {
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

export function PortraitLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} />
    </>
  );
}
