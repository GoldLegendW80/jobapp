/** split-right: api/app/documents/templates/split-right, a narrow column on the right. */
import {
  ContactAcross,
  ContactColumn,
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
            <ContactAcross page={page} />
          </Lines>
        </div>
        <Portrait page={page} />
      </Lines>
    </header>
  );
}

export function SplitRightCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <div className="cols">
        <Lines>
          <main className="main">
            <Lines>
              <SummaryBlock page={page} />
              <ListBlock page={page} part="highlights" />
              <ExperienceBlock page={page} />
            </Lines>
          </main>
          <aside className="side">
            <Lines>
              <ContactColumn page={page} heading="side-label" />
              <ListBlock page={page} part="competencies" label="side-label" />
              <SchoolsBlock page={page} label="side-label" />
              <SkillsBlock page={page} label="side-label" />
              <LanguagesBlock page={page} label="side-label" />
            </Lines>
          </aside>
        </Lines>
      </div>
    </>
  );
}

export function SplitRightLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <div className="cols">
        <Lines>
          <LetterBody page={page} as="main" className="main letter-body" />
          <aside className="side">
            <Lines>
              <ContactColumn page={page} heading="side-label" />
            </Lines>
          </aside>
        </Lines>
      </div>
    </>
  );
}
