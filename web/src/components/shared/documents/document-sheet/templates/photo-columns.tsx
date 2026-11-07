/** photo-columns: api/app/documents/templates/photo-columns, the photo on top, two columns. */
import {
  codePoints,
  nameClass,
  shows,
  type SheetPage,
  type SheetRole,
} from "@/features/documents/helpers/sheet-page";
import {
  ContactLines,
  Empty,
  ExperienceBlock,
  Headline,
  LetterBody,
  ListBlock,
  Name,
  Portrait,
  Region,
  SchoolsBlock,
  SummaryBlock,
  type LayoutProps,
  Lines,
} from "../parts";

function Head({ page }: LayoutProps) {
  return (
    <>
      <header className="head">
        <Lines>
          <Portrait page={page} />
          <div className="who">
            <Lines>
              <Name page={page} className={nameClass(page.name)} />
              <Headline page={page} />
            </Lines>
          </div>
          <ContactLines page={page} />
        </Lines>
      </header>
      <div className="rule" />
    </>
  );
}

/** The left column widens to half the page when one of its texts is over 60 characters. */
function colsClass(page: SheetPage): string {
  const left = [
    ...page.lists.competencies,
    ...page.schools.map((one) => one.degree).filter(Boolean),
    ...page.schools.map((one) => one.school).filter(Boolean),
    ...page.skills.flatMap((one) => one.items),
  ];
  return left.some((one) => codePoints(one) > 60) ? "cols long" : "cols";
}

function School({ item }: { item: SheetPage["schools"][number] }) {
  return (
    <article className="entry">
      <Lines>
        <h3 aria-level={4} className="title">
          {item.degree}
        </h3>
        {item.school || item.location ? (
          <p className="org">
            {item.school}
            {item.school && item.location ? ", " : ""}
            {item.location}
          </p>
        ) : null}
        {item.period ? <p className="period">{item.period}</p> : null}
      </Lines>
    </article>
  );
}

/** A group of four items or fewer as a list, a longer one on one line. */
function Group({ group }: { group: { label: string; items: string[] } }) {
  return (
    <div className="group">
      <Lines>
        <h3 aria-level={4} className="title">
          {group.label}
        </h3>
        {group.items.length <= 4 ? (
          <ul className="lines">
            <Lines>
              {group.items.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </Lines>
          </ul>
        ) : (
          <p className="lines">{group.items.join(", ")}</p>
        )}
      </Lines>
    </div>
  );
}

function SpokenLanguages({ page }: LayoutProps) {
  if (page.languages.length === 0) return null;
  const between = page.language === "fr" ? " : " : ": ";
  return (
    <div className="group">
      <Lines>
        <h3 aria-level={4} className="title">
          {page.labels.languages}
        </h3>
        <ul className="lines">
          <Lines>
            {page.languages.map((item, index) => (
              <li key={index}>
                {item.name}
                {item.level ? `${between}${item.level}` : ""}
              </li>
            ))}
          </Lines>
        </ul>
      </Lines>
    </div>
  );
}

/** The skill groups, then the spoken languages, under the skills' heading. */
function Skills({ page }: LayoutProps) {
  const written = shows(page, "skills");
  if (!written && page.languages.length === 0) return null;
  const heading = (
    <h2 aria-level={3} className="heading">
      {page.labels.skills}
    </h2>
  );
  if (!written)
    return (
      <section className="block">
        <Lines>
          {heading}
          <SpokenLanguages page={page} />
        </Lines>
      </section>
    );
  return (
    <Region page={page} part="skills" as="section" className="block">
      <Lines>
        {heading}
        {page.skills.length === 0 ? <Empty>Add a group of skills</Empty> : null}
        {page.skills.map((one, index) => (
          <Group key={index} group={one} />
        ))}
        <SpokenLanguages page={page} />
      </Lines>
    </Region>
  );
}

function Role({ role }: { role: SheetRole }) {
  const place = `${role.organisation}${role.organisation && role.location ? ", " : ""}${role.location}`;
  return (
    <article className="entry role">
      <Lines>
        <h3 aria-level={4} className="title">
          {role.title}
        </h3>
        <p className="org">
          {place}
          {role.period ? `${place ? " | " : ""}${role.period}` : ""}
        </p>
        {role.bullets.map((one, index) =>
          one.trim() === "" ? null : (
            <p
              key={index}
              className="text"
              data-edit={`Bullet ${String(index + 1)}, ${role.organisation}`}
            >
              {one}
            </p>
          ),
        )}
      </Lines>
    </article>
  );
}

export function PhotoColumnsCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <SummaryBlock page={page} section="profile" label="heading" />
      <div className={colsClass(page)}>
        <Lines>
          <div className="col left">
            <Lines>
              <SchoolsBlock
                page={page}
                label="heading"
                school={(item) => <School item={item} />}
              />
              <Skills page={page} />
              <ListBlock
                page={page}
                part="competencies"
                label="heading"
                list="dots"
              />
            </Lines>
          </div>
          <div className="col right">
            <Lines>
              <ExperienceBlock
                page={page}
                label="heading"
                entry={(role) => <Role role={role} />}
              />
              <ListBlock
                page={page}
                part="highlights"
                label="heading"
                list="dots"
              />
            </Lines>
          </div>
        </Lines>
      </div>
    </>
  );
}

export function PhotoColumnsLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} />
    </>
  );
}
