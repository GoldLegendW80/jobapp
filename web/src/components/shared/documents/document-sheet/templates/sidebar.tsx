/** sidebar: api/app/documents/templates/sidebar, a tinted column on the left. */
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
        <Name page={page} />
        <Headline page={page} />
        <ContactAcross page={page} />
      </Lines>
    </header>
  );
}

export function SidebarCv({ page }: LayoutProps) {
  return (
    <div className="page">
      <Lines>
        <main className="main">
          <Lines>
            <Head page={page} />
            <SummaryBlock page={page} />
            <ListBlock page={page} part="highlights" />
            <ExperienceBlock page={page} />
            <SchoolsBlock page={page} wrap="schools" />
          </Lines>
        </main>
        <aside className="side">
          <Lines>
            <Portrait page={page} />
            <ContactColumn page={page} />
            <ListBlock page={page} part="competencies" />
            <SkillsBlock page={page} section="block skills" />
            <LanguagesBlock page={page} />
          </Lines>
        </aside>
      </Lines>
    </div>
  );
}

export function SidebarLetter({ page }: LayoutProps) {
  return (
    <div className="page">
      <Lines>
        <main className="main">
          <Lines>
            <Head page={page} />
            <LetterBody page={page} />
          </Lines>
        </main>
        <aside className="side">
          <Lines>
            <Portrait page={page} />
            <ContactColumn page={page} />
          </Lines>
        </aside>
      </Lines>
    </div>
  );
}
