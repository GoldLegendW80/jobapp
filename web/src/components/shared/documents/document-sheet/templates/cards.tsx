/** cards: api/app/documents/templates/cards, every section and each role on a soft card. */
import { rolesShown } from "@/features/documents/helpers/sheet-page";
import {
  ContactLines,
  Empty,
  Employer,
  Headline,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  Name,
  Portrait,
  Region,
  SchoolsBlock,
  SkillsBlock,
  SummaryBlock,
  type LayoutProps,
  Lines,
} from "../parts";

function Head({ page }: LayoutProps) {
  return (
    <header className="card head">
      <Lines>
        <Portrait page={page} />
        <div className="headtext">
          <Lines>
            <Name page={page} words="w" />
            <Headline page={page} />
            <ContactLines page={page} />
          </Lines>
        </div>
      </Lines>
    </header>
  );
}

/** Each role on a card of its own, the first one under the heading. */
function Roles({ page }: LayoutProps) {
  const roles = rolesShown(page);
  const heading = (
    <h2 aria-level={3} className="label">
      {page.labels.experience}
    </h2>
  );
  return (
    <Region page={page} part="experience" as="section" className="roles">
      {roles.length === 0 ? (
        <div className="card">
          <Lines>
            {heading}
            <Empty>Add an employer</Empty>
          </Lines>
        </div>
      ) : (
        roles.map((role, index) => (
          <div key={index} className="card">
            {index === 0 ? heading : null}
            <Employer role={role} />
          </div>
        ))
      )}
    </Region>
  );
}

export function CardsCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <div className="columns">
        <Lines>
          <main className="main">
            <Lines>
              <SummaryBlock page={page} section="card" />
              <ListBlock page={page} part="highlights" section="card" />
              <Roles page={page} />
            </Lines>
          </main>
          <aside className="side">
            <Lines>
              <ListBlock page={page} part="competencies" section="card" />
              <SchoolsBlock page={page} section="card" />
              <SkillsBlock page={page} section="card" />
              <LanguagesBlock page={page} section="card" />
            </Lines>
          </aside>
        </Lines>
      </div>
    </>
  );
}

export function CardsLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} className="card letter-body" words="w" />
    </>
  );
}
