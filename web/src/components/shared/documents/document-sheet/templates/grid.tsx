/** grid: api/app/documents/templates/grid, tinted strips and tags. */
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
  return (
    <header className="head">
      <Lines>
        <div className="headtext">
          <Lines>
            <Name page={page} />
            <Headline page={page} />
            <ContactLines page={page} className="contact tags" />
          </Lines>
        </div>
        <Portrait page={page} />
      </Lines>
    </header>
  );
}

export function GridCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <SummaryBlock page={page} />
      <ListBlock page={page} part="highlights" />
      <ListBlock page={page} part="competencies" />
      <ExperienceBlock page={page} />
      <SchoolsBlock page={page} wrap="schools" />
      <SkillsBlock
        page={page}
        group={(one) => (
          <div className="skill-group">
            <Lines>
              <h3 aria-level={4} className="group-label">
                {one.label}
              </h3>
              <ul className="tags">
                <Lines>
                  {one.items.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </Lines>
              </ul>
            </Lines>
          </div>
        )}
      />
      <LanguagesBlock page={page} list="tags languages" />
    </>
  );
}

export function GridLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} />
    </>
  );
}
