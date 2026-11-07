/** band: api/app/documents/templates/band, a dark band on top, each label on the left. */
import type { ReactNode } from "react";

import {
  LISTS,
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

function Band({ page }: LayoutProps) {
  return (
    <header className="band">
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
  );
}

/** The languages after the competencies, in the same list: "Languages: French (native), ...". */
function LanguagesLine({ page }: LayoutProps) {
  if (page.languages.length === 0) return null;
  const said = page.languages
    .map((one) => (one.level ? `${one.name} (${one.level})` : one.name))
    .join(", ");
  return (
    <li className="langs">
      {page.labels.languages}
      {page.language === "fr" ? " :" : ":"} {said}
    </li>
  );
}

/** The competencies and the languages under one label, then a rule. */
function Competencies({ page }: LayoutProps) {
  const written = shows(page, "competencies");
  if (!written && page.languages.length === 0) return null;
  const items = page.lists.competencies.filter((one) => one.trim() !== "");
  const heading = (
    <h2 aria-level={3} className="label">
      {page.labels.competencies}
    </h2>
  );
  const list: ReactNode = (
    <ul className="dots">
      <Lines>
        {items.map((item, index) => (
          <li key={index} data-edit={`Competency ${String(index + 1)}`}>
            {item}
          </li>
        ))}
        <LanguagesLine page={page} />
      </Lines>
    </ul>
  );
  return (
    <>
      {written ? (
        <Region page={page} part="competencies" as="section" className="row">
          <Lines>
            {heading}
            <div className="body">
              <Lines>
                {items.length === 0 ? (
                  <Empty>{LISTS.competencies.empty}</Empty>
                ) : null}
                {items.length > 0 || page.languages.length > 0 ? list : null}
              </Lines>
            </div>
          </Lines>
        </Region>
      ) : (
        <section className="row">
          <Lines>
            {heading}
            <div className="body">{list}</div>
          </Lines>
        </section>
      )}
      <div className="rule" />
    </>
  );
}

function Groups({ page }: LayoutProps) {
  if (!shows(page, "skills")) return null;
  return (
    <Region page={page} part="skills" as="div">
      <Lines>
        {page.skills.length === 0 ? (
          <section className="row group">
            <Lines>
              <h2 aria-level={3} className="label">
                {page.labels.skills}
              </h2>
              <div className="body">
                <Empty>Add a group of skills</Empty>
              </div>
            </Lines>
          </section>
        ) : (
          page.skills.map((group, index) => (
            <section key={index} className="row group">
              <Lines>
                <h2 aria-level={3} className="label">
                  {group.label}
                </h2>
                <div className="body">
                  <p className="items">{group.items.join(", ")}</p>
                </div>
              </Lines>
            </section>
          ))
        )}
      </Lines>
    </Region>
  );
}

function Role({ role }: { role: SheetRole }) {
  return (
    <article className="entry">
      <Lines>
        <h3 aria-level={4} className="title">
          {role.title}
          {role.title && role.organisation ? ", " : ""}
          {role.organisation}
        </h3>
        {role.period ? <p className="meta">{role.period}</p> : null}
        {role.location ? <p className="meta">{role.location}</p> : null}
        {role.bullets.some((one) => one.trim() !== "") ? (
          <ul className="dots">
            <Lines>
              {role.bullets.map((one, index) =>
                one.trim() === "" ? null : (
                  <li
                    key={index}
                    data-edit={`Bullet ${String(index + 1)}, ${role.organisation}`}
                  >
                    {one}
                  </li>
                ),
              )}
            </Lines>
          </ul>
        ) : null}
      </Lines>
    </article>
  );
}

function School({ item }: { item: SheetPage["schools"][number] }) {
  return (
    <article className="entry school">
      <Lines>
        <h3 aria-level={4} className="title">
          {item.degree}
        </h3>
        {item.school ? (
          <p className="meta">
            {item.school}
            {item.location ? `, ${item.location}` : ""}
          </p>
        ) : item.location ? (
          <p className="meta">{item.location}</p>
        ) : null}
        {item.period ? <p className="meta">{item.period}</p> : null}
      </Lines>
    </article>
  );
}

export function BandCv({ page }: LayoutProps) {
  return (
    <>
      <Band page={page} />
      <SummaryBlock page={page} section="row" wrap="body" />
      <Competencies page={page} />
      <ExperienceBlock
        page={page}
        section="row"
        wrap="body"
        entry={(role) => <Role role={role} />}
      />
      {page.schools.length > 0 ? (
        <>
          <SchoolsBlock
            page={page}
            section="row"
            wrap="body"
            school={(item) => <School item={item} />}
          />
          <div className="rule" />
        </>
      ) : null}
      <ListBlock
        page={page}
        part="highlights"
        section="row"
        wrap="body"
        list="dots"
      />
      <Groups page={page} />
    </>
  );
}

export function BandLetter({ page }: LayoutProps) {
  return (
    <>
      <Band page={page} />
      <LetterBody page={page} />
    </>
  );
}
