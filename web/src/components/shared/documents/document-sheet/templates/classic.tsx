/** classic: api/app/documents/templates/classic, each section's label in a column on its left. */
import { shows } from "@/features/documents/helpers/sheet-page";
import {
  BaseHeader,
  Language,
  LetterBody,
  ListBlock,
  Region,
  Roles,
  School,
  SkillGroups,
  TextRegion,
  type LayoutProps,
  Lines,
} from "../parts";

export function ClassicCv({ page }: LayoutProps) {
  const lists = shows(page, "highlights") || shows(page, "competencies");
  return (
    <>
      <div className="bar" />
      <BaseHeader page={page} />
      <div className="bar" />
      {shows(page, "summary") || lists ? (
        <section className="row">
          <Lines>
            <h2 aria-level={3} className="label">
              {page.labels.summary}
            </h2>
            <div className="body">
              <Lines>
                <TextRegion page={page} part="summary" />
                {lists ? (
                  <div className="about-cols">
                    <Lines>
                      <ListBlock
                        page={page}
                        part="highlights"
                        as="div"
                        heading="h3"
                        section=""
                        label="sub"
                        list={null}
                      />
                      <ListBlock
                        page={page}
                        part="competencies"
                        as="div"
                        heading="h3"
                        section=""
                        label="sub"
                      />
                    </Lines>
                  </div>
                ) : null}
              </Lines>
            </div>
          </Lines>
        </section>
      ) : null}
      {shows(page, "experience") ? (
        <section className="row">
          <Lines>
            <h2 aria-level={3} className="label">
              {page.labels.experience}
            </h2>
            <Region page={page} part="experience" as="div" className="body">
              <Roles page={page} />
            </Region>
          </Lines>
        </section>
      ) : null}
      {page.schools.length > 0 ? (
        <section className="row">
          <Lines>
            <h2 aria-level={3} className="label">
              {page.labels.education}
            </h2>
            <div className="body schools">
              <Lines>
                {page.schools.map((item, index) => (
                  <School key={index} item={item} />
                ))}
              </Lines>
            </div>
          </Lines>
        </section>
      ) : null}
      {shows(page, "skills") ? (
        <section className="row">
          <Lines>
            <h2 aria-level={3} className="label">
              {page.labels.skills}
            </h2>
            <Region page={page} part="skills" as="div" className="body skills">
              <SkillGroups page={page} />
            </Region>
          </Lines>
        </section>
      ) : null}
      {page.languages.length > 0 ? (
        <section className="row">
          <Lines>
            <h2 aria-level={3} className="label">
              {page.labels.languages}
            </h2>
            <div className="body">
              <Lines>
                <ul className="languages">
                  {page.languages.map((item, index) => (
                    <Language key={index} item={item} />
                  ))}
                </ul>
              </Lines>
            </div>
          </Lines>
        </section>
      ) : null}
    </>
  );
}

export function ClassicLetter({ page }: LayoutProps) {
  return (
    <>
      <div className="bar" />
      <BaseHeader page={page} />
      <div className="bar" />
      <LetterBody page={page} />
    </>
  );
}
