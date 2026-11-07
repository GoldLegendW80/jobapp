/** ledger: api/app/documents/templates/ledger, heavy rules, each body on a grid of cells. */
import {
  codePoints,
  nameClass,
  shows,
  type SheetPage,
  type SheetRole,
} from "@/features/documents/helpers/sheet-page";
import {
  ContactLines,
  ExperienceBlock,
  Headline,
  LetterBody,
  ListBlock,
  Name,
  Portrait,
  SchoolsBlock,
  SkillsBlock,
  TextRegion,
  type LayoutProps,
  Lines,
} from "../parts";

/** A cell over 40 characters takes the whole row. */
const cell = (texts: string[], base = "cell") =>
  texts.some((one) => codePoints(one) > 40) ? `${base} wide` : base;

function Head({ page }: LayoutProps) {
  return (
    <>
      <div className="heavy" />
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
      <div className="heavy" />
    </>
  );
}

/** The profile, with the highlights and the competencies side by side under it. */
function About({ page }: LayoutProps) {
  const pair = shows(page, "highlights") || shows(page, "competencies");
  if (!shows(page, "summary") && !pair) return null;
  return (
    <section className="row">
      <Lines>
        <h2 aria-level={3} className="label">
          {page.labels.summary}
        </h2>
        <div className="body">
          <Lines>
            <TextRegion page={page} part="summary" />
            {pair ? (
              <div className="pair">
                <Lines>
                  <ListBlock
                    page={page}
                    part="highlights"
                    as="div"
                    heading="h3"
                    section="half"
                    label="title"
                    list="lines"
                  />
                  <ListBlock
                    page={page}
                    part="competencies"
                    as="div"
                    heading="h3"
                    section="half"
                    label="title"
                    list="lines two"
                  />
                </Lines>
              </div>
            ) : null}
          </Lines>
        </div>
      </Lines>
    </section>
  );
}

function Role({ role }: { role: SheetRole }) {
  return (
    <article className="role">
      <Lines>
        <h3 aria-level={4} className="title">
          {role.title}
          {role.title && role.organisation ? ", " : ""}
          {role.organisation}
        </h3>
        {role.period || role.location ? (
          <p className="meta">
            {role.period}
            {role.period && role.location ? ", " : ""}
            {role.location}
          </p>
        ) : null}
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

function Group({ group }: { group: { label: string; items: string[] } }) {
  return (
    <div className={cell([...group.items, group.label])}>
      <Lines>
        <h3 aria-level={4} className="title">
          {group.label}
        </h3>
        <ul className="lines">
          <Lines>
            {group.items.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </Lines>
        </ul>
      </Lines>
    </div>
  );
}

function School({ item }: { item: SheetPage["schools"][number] }) {
  return (
    <div className={cell([item.degree, item.school, item.location])}>
      <Lines>
        <h3 aria-level={4} className="title">
          {item.degree}
        </h3>
        <ul className="lines">
          <Lines>
            {item.school ? <li>{item.school}</li> : null}
            {item.period || item.location ? (
              <li>
                {item.period}
                {item.period && item.location ? ", " : ""}
                {item.location}
              </li>
            ) : null}
          </Lines>
        </ul>
      </Lines>
    </div>
  );
}

function Languages({ page }: LayoutProps) {
  if (page.languages.length === 0) return null;
  return (
    <section className="row">
      <Lines>
        <h2 aria-level={3} className="label">
          {page.labels.languages}
        </h2>
        <div className="body cells">
          <Lines>
            {page.languages.map((item, index) => (
              <p
                key={index}
                className={cell(
                  [`${item.name}: ${item.level ?? ""}`],
                  "cell lang",
                )}
              >
                {item.name}
                {item.level ? `: ${item.level}` : ""}
              </p>
            ))}
          </Lines>
        </div>
      </Lines>
    </section>
  );
}

export function LedgerCv({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <About page={page} />
      <ExperienceBlock
        page={page}
        section="row"
        wrap="body"
        entry={(role) => <Role role={role} />}
      />
      <SkillsBlock
        page={page}
        section="row"
        wrap="body cells"
        group={(one) => <Group group={one} />}
      />
      <SchoolsBlock
        page={page}
        section="row"
        wrap="body cells"
        school={(item) => <School item={item} />}
      />
      <Languages page={page} />
      <div className="heavy" />
    </>
  );
}

export function LedgerLetter({ page }: LayoutProps) {
  return (
    <>
      <Head page={page} />
      <LetterBody page={page} />
      <div className="heavy" />
    </>
  );
}
