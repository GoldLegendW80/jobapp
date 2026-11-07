/**
 * The parts every template's page is made of, with the markup and classes of
 * api/app/documents/templates/_base/macros.html.j2 and layout.html.j2, so the template's own
 * style.css draws them; the parts the person writes are `Region`s, edited where they are read.
 */
import { Children, Fragment, type ElementType, type ReactNode } from "react";

import {
  codePoints,
  isList,
  isText,
  LISTS,
  partName,
  partProblems,
  rolesShown,
  shows,
  written,
  type ListPart,
  type Part,
  type SheetPage,
  type SheetRole,
  type SheetSchool,
  type TextPart,
  TEXTS,
} from "@/features/documents/helpers/sheet-page";
import { ListField, TextField } from "../edit-fields";
import { ExperienceSection } from "../edit-forms/experience-section";
import { SkillsSection } from "../edit-forms/skills-section";
import { InPlace } from "../in-place";

/** Siblings on lines of their own, as each template prints its tags: inline, a new line is a space. */
export function Lines({ children }: { children: ReactNode }) {
  return (
    <>
      {Children.toArray(children).flatMap((one, index) =>
        index === 0 ? [one] : ["\n", one],
      )}
    </>
  );
}

export interface LayoutProps {
  page: SheetPage;
}

function editorOf(page: SheetPage, part: Part): ReactNode {
  const form = page.form;
  if (form === null) return null;
  if (isText(part))
    return (
      <TextField
        form={form}
        path={[part]}
        label={TEXTS[part].label}
        max={TEXTS[part].max}
        multiline={TEXTS[part].multiline}
        hideLabel
      />
    );
  if (isList(part))
    return (
      <ListField
        form={form}
        path={[part]}
        legend={LISTS[part].legend}
        noun={LISTS[part].noun}
        max={LISTS[part].max}
        itemMax={LISTS[part].itemMax}
        multiline={LISTS[part].multiline}
        hideLegend
      />
    );
  if (part === "experience")
    return (
      <ExperienceSection
        form={form}
        employers={page.employers}
        known={page.known}
      />
    );
  return <SkillsSection form={form} />;
}

/**
 * A part the person writes, as the template's own element (`as`, `className`): left out of a
 * page only read when it has no text, as the template leaves it out.
 */
export function Region({
  page,
  part,
  as,
  className,
  children,
}: LayoutProps & {
  part: Part;
  as: ElementType;
  className?: string;
  children: ReactNode;
}) {
  if (!shows(page, part)) return null;
  return (
    <InPlace
      as={as}
      className={className}
      label={partName(part)}
      paths={[[part]]}
      problems={partProblems(page, part)}
      editor={editorOf(page, part)}
    >
      {children}
    </InPlace>
  );
}

/** What to click to write an empty part, in the page's own type. */
export function Empty({ children }: { children: ReactNode }) {
  return <span className="sheet-empty">{children}</span>;
}

/** A text part's words, or what to click to write it. */
export function Text({ page, part }: LayoutProps & { part: TextPart }) {
  const value = page.text[part];
  return value.trim() === "" ? <Empty>{TEXTS[part].empty}</Empty> : value;
}

/** A text part as one element of the template: `<p class="summary">`. */
export function TextRegion({
  page,
  part,
  as = "p",
  className = part,
}: LayoutProps & { part: TextPart; as?: ElementType; className?: string }) {
  return (
    <Region page={page} part={part} as={as} className={className}>
      <Lines>
        <Text page={page} part={part} />
      </Lines>
    </Region>
  );
}

/** `base.words`: each word of a name in a box of its own. */
export function Words({
  text,
  className = "word",
}: {
  text: string;
  className?: string;
}) {
  return (
    <>
      {text
        .split(" ")
        .filter((word) => word !== "")
        .map((word, index) => (
          <Fragment key={index}>
            {index === 0 ? null : " "}
            <span className={className}>{word}</span>
          </Fragment>
        ))}
    </>
  );
}

/** `<h1 class="name">` with the profile's name; while edited, a page without one says so. */
export function Name({
  page,
  words,
  className = "name",
}: LayoutProps & { words?: string; className?: string }) {
  return (
    <h1 className={className} role="presentation">
      {page.name === "" && page.form !== null ? (
        <Empty>Your name</Empty>
      ) : (
        <Words text={page.name} className={words} />
      )}
    </h1>
  );
}

export function Headline({ page }: LayoutProps) {
  return <TextRegion page={page} part="headline" />;
}

/** `base.contact_lines`: links print as the template styles them, without leaving the page. */
export function ContactLines({
  page,
  className = "contact",
}: LayoutProps & { className?: string }) {
  if (page.contact.length === 0) return null;
  return (
    <ul className={className}>
      <Lines>
        {page.contact.map((line) => (
          <li key={line.text} className={line.kind}>
            {line.link ? <a>{line.text}</a> : line.text}
          </li>
        ))}
      </Lines>
    </ul>
  );
}

/** `layout.contact_cells`: a grid of three, each line one, two or three cells by its length. */
export function ContactCells({
  page,
  cell,
  pair,
}: LayoutProps & { cell: number; pair: number }) {
  if (page.contact.length === 0) return null;
  return (
    <ul className="contact">
      <Lines>
        {page.contact.map((line) => {
          const size = codePoints(line.text);
          const span = size > pair ? 3 : size > cell ? 2 : 1;
          return (
            <li key={line.text} className={`span${String(span)}`}>
              {line.link ? <a>{line.text}</a> : line.text}
            </li>
          );
        })}
      </Lines>
    </ul>
  );
}

const longest = (page: SheetPage) =>
  Math.max(0, ...page.contact.map((line) => codePoints(line.text)));

/** `layout.contact_column`: the lines of a narrow column, smaller past 25 characters, none past 31. */
export function ContactColumn({
  page,
  heading = "label",
}: LayoutProps & { heading?: string }) {
  const [small, wide] = page.kind === "cv" ? [25, 31] : [23, 28];
  const size = longest(page);
  if (page.contact.length === 0 || size > wide) return null;
  return (
    <section className="block">
      <Lines>
        <h2 aria-level={3} className={heading}>
          {page.labels.contact}
        </h2>
        <ContactLines
          page={page}
          className={size > small ? "contact small" : "contact"}
        />
      </Lines>
    </section>
  );
}

/** `layout.contact_across`: the lines too long for the column, in a row under the headline. */
export function ContactAcross({ page }: LayoutProps) {
  const wide = page.kind === "cv" ? 31 : 28;
  if (page.contact.length === 0 || longest(page) <= wide) return null;
  return <ContactLines page={page} className="contact across" />;
}

/** `base.portrait`: the profile's photo, when the template has a place for it. */
export function Portrait({
  page,
  className = "photo",
}: LayoutProps & { className?: string }) {
  if (page.photo === null) return null;
  return <img className={className} src={page.photo} alt={page.name} />;
}

/** `base.header`: the photo, the name, the headline and the contact lines. */
export function BaseHeader({ page, words }: LayoutProps & { words?: string }) {
  return (
    <header className="head">
      <Lines>
        <Portrait page={page} />
        <div className="headtext">
          <Lines>
            <Name page={page} words={words} />
            <Headline page={page} />
            <ContactLines page={page} />
          </Lines>
        </div>
      </Lines>
    </header>
  );
}

/** `<header class="head">` with the name, the headline and the contact lines, one under the other. */
export function StackedHeader({
  page,
  words,
}: LayoutProps & { words?: string }) {
  return (
    <header className="head">
      <Lines>
        <Name page={page} words={words} />
        <Headline page={page} />
        <ContactLines page={page} />
      </Lines>
    </header>
  );
}

/** A role with its period and place under its title (minimal, timeline). */
export function DatedEntry({ role }: { role: SheetRole }) {
  return (
    <article className="entry">
      <Lines>
        <h3 aria-level={4} className="entry-title">
          {role.title}
          {role.title && role.organisation ? ", " : ""}
          <span className="org">{role.organisation}</span>
        </h3>
        {role.period ? <p className="period">{role.period}</p> : null}
        {role.location ? <p className="place">{role.location}</p> : null}
        <RoleBullets role={role} />
      </Lines>
    </article>
  );
}

/** `base.bullets` of a list part: each item says which field a click opens. */
export function Bullets({
  page,
  part,
  className,
}: LayoutProps & { part: ListPart; className?: string }) {
  const items = page.lists[part];
  if (!written(page, part)) return <Empty>{LISTS[part].empty}</Empty>;
  return (
    <ul className={className}>
      <Lines>
        {items.map((item, index) =>
          item.trim() === "" ? null : (
            <li
              key={index}
              data-edit={`${LISTS[part].noun} ${String(index + 1)}`}
            >
              {item}
            </li>
          ),
        )}
      </Lines>
    </ul>
  );
}

/** A role's bullets, each naming its field for a click. */
export function RoleBullets({ role }: { role: SheetRole }) {
  if (!role.bullets.some((one) => one.trim() !== "")) return null;
  return (
    <ul>
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
  );
}

/** `base.employer`. */
export function Employer({ role }: { role: SheetRole }) {
  return (
    <article className="entry">
      <Lines>
        <div className="entry-head">
          <Lines>
            <h3 aria-level={4} className="entry-title">
              {role.title}
              {role.title && role.organisation ? ", " : ""}
              <span className="org">{role.organisation}</span>
            </h3>
            {role.period ? <p className="period">{role.period}</p> : null}
          </Lines>
        </div>
        {role.location ? <p className="place">{role.location}</p> : null}
        <RoleBullets role={role} />
      </Lines>
    </article>
  );
}

/** `base.school`. */
export function School({ item }: { item: SheetSchool }) {
  return (
    <div className="school">
      <Lines>
        <h3 aria-level={4} className="entry-title">
          {item.degree}
        </h3>
        <p className="org">{item.school}</p>
        {item.period || item.location ? (
          <p className="period">
            {item.period}
            {item.period && item.location ? ", " : ""}
            {item.location}
          </p>
        ) : null}
      </Lines>
    </div>
  );
}

/** `base.language`. */
export function Language({
  item,
}: {
  item: { name: string; level: string | null };
}) {
  return (
    <li>
      <strong>{item.name}</strong>
      {item.level ? `: ${item.level}` : ""}
    </li>
  );
}

/** `base.skill_group`. */
export function SkillGroup({
  group,
}: {
  group: { label: string; items: string[] };
}) {
  return (
    <div className="skill-group">
      <Lines>
        <h3 aria-level={4} className="group-label">
          {group.label}
        </h3>
        <p className="items">{group.items.join(", ")}</p>
      </Lines>
    </div>
  );
}

function wrapped(wrap: string | undefined, children: ReactNode) {
  return wrap === undefined ? children : <div className={wrap}>{children}</div>;
}

interface BlockProps extends LayoutProps {
  /** The section's class: `block` in most templates. */
  section?: string;
  /** The heading's class: `label` in most templates. */
  label?: string;
  /** A box of this class around what follows the heading. */
  wrap?: string;
}

/** `<section class="block"><h2 aria-level={3} class="label">Profile</h2><p class="summary">`. */
export function SummaryBlock({
  page,
  section = "block",
  label = "label",
  wrap,
}: BlockProps) {
  if (!shows(page, "summary")) return null;
  return (
    <section className={section}>
      <Lines>
        <h2 aria-level={3} className={label}>
          {page.labels.summary}
        </h2>
        {wrapped(wrap, <TextRegion page={page} part="summary" />)}
      </Lines>
    </section>
  );
}

/** The achievements or the key competencies under their heading, the section edited as one. */
export function ListBlock({
  page,
  part,
  as = "section",
  heading: Heading = "h2",
  section = "block",
  label = "label",
  wrap,
  list = part,
  joined = false,
}: BlockProps & {
  part: "highlights" | "competencies";
  as?: ElementType;
  heading?: ElementType;
  /** The list's class; null: none (`base.bullets(content.highlights)`). */
  list?: string | null;
  /** One line of text, the items joined by commas. */
  joined?: boolean;
}) {
  const items = joined ? (
    written(page, part) ? (
      <p className={list ?? undefined}>
        {page.lists[part].filter((one) => one.trim() !== "").join(", ")}
      </p>
    ) : (
      <Empty>{LISTS[part].empty}</Empty>
    )
  ) : (
    <Bullets page={page} part={part} className={list ?? undefined} />
  );
  return (
    <Region page={page} part={part} as={as} className={section || undefined}>
      <Lines>
        <Heading className={label} aria-level={Heading === "h3" ? 4 : 3}>
          {page.labels[part]}
        </Heading>
        {wrapped(wrap, items)}
      </Lines>
    </Region>
  );
}

/** The roles printed, or what to click to add one. */
export function Roles({
  page,
  entry = (role) => <Employer role={role} />,
}: LayoutProps & { entry?: (role: SheetRole) => ReactNode }) {
  const roles = rolesShown(page);
  if (roles.length === 0) return <Empty>Add an employer</Empty>;
  return (
    <>
      {roles.map((role, index) => (
        <Fragment key={index}>{entry(role)}</Fragment>
      ))}
    </>
  );
}

/** The experience under its heading, edited as one. */
export function ExperienceBlock({
  page,
  section = "block",
  label = "label",
  wrap,
  entry,
}: BlockProps & { entry?: (role: SheetRole) => ReactNode }) {
  return (
    <Region page={page} part="experience" as="section" className={section}>
      <Lines>
        <h2 aria-level={3} className={label}>
          {page.labels.experience}
        </h2>
        {wrapped(wrap, <Roles page={page} entry={entry} />)}
      </Lines>
    </Region>
  );
}

/** The schools of the profile under their heading (printed as they are). */
export function SchoolsBlock({
  page,
  section = "block",
  label = "label",
  wrap,
  school = (item) => <School item={item} />,
}: BlockProps & { school?: (item: SheetSchool) => ReactNode }) {
  if (page.schools.length === 0) return null;
  return (
    <section className={section}>
      <Lines>
        <h2 aria-level={3} className={label}>
          {page.labels.education}
        </h2>
        {wrapped(
          wrap,
          page.schools.map((item, index) => (
            <Fragment key={index}>{school(item)}</Fragment>
          )),
        )}
      </Lines>
    </section>
  );
}

/** The groups of skills, or what to click to add one. */
export function SkillGroups({
  page,
  group = (one) => <SkillGroup group={one} />,
}: LayoutProps & {
  group?: (one: { label: string; items: string[] }) => ReactNode;
}) {
  if (page.skills.length === 0) return <Empty>Add a group of skills</Empty>;
  return (
    <>
      {page.skills.map((one, index) => (
        <Fragment key={index}>{group(one)}</Fragment>
      ))}
    </>
  );
}

/** The skills under their heading, edited as one. */
export function SkillsBlock({
  page,
  section = "block",
  label = "label",
  wrap,
  group,
}: BlockProps & {
  group?: (one: { label: string; items: string[] }) => ReactNode;
}) {
  return (
    <Region page={page} part="skills" as="section" className={section}>
      <Lines>
        <h2 aria-level={3} className={label}>
          {page.labels.skills}
        </h2>
        {wrapped(wrap, <SkillGroups page={page} group={group} />)}
      </Lines>
    </Region>
  );
}

/** The languages of the profile under their heading (printed as they are). */
export function LanguagesBlock({
  page,
  section = "block",
  label = "label",
  wrap,
  list = "languages",
}: BlockProps & { list?: string }) {
  if (page.languages.length === 0) return null;
  return (
    <section className={section}>
      <Lines>
        <h2 aria-level={3} className={label}>
          {page.labels.languages}
        </h2>
        {wrapped(
          wrap,
          <ul className={list}>
            <Lines>
              {page.languages.map((item, index) => (
                <Language key={index} item={item} />
              ))}
            </Lines>
          </ul>,
        )}
      </Lines>
    </section>
  );
}

/** The paragraphs of a letter, edited as one: each a `<p class="paragraph">`. */
export function Paragraphs({ page }: LayoutProps) {
  const items = page.lists.paragraphs;
  return (
    <Region page={page} part="paragraphs" as="div">
      <Lines>
        {written(page, "paragraphs") ? (
          items.map((one, index) =>
            one.trim() === "" ? null : (
              <p
                key={index}
                className="paragraph"
                data-edit={`Paragraph ${String(index + 1)}`}
              >
                {one}
              </p>
            ),
          )
        ) : (
          <p className="paragraph">
            <Empty>{LISTS.paragraphs.empty}</Empty>
          </p>
        )}
      </Lines>
    </Region>
  );
}

/** `<p class="subject"><strong>Subject:</strong> ...</p>`. */
export function Subject({ page }: LayoutProps) {
  return (
    <Region page={page} part="subject" as="p" className="subject">
      {written(page, "subject") ? (
        <>
          <strong>{page.labels.subject}</strong> {page.text.subject}
        </>
      ) : (
        <Empty>{TEXTS.subject.empty}</Empty>
      )}
    </Region>
  );
}

/** The letter's date, recipient and subject: the start of every letter. */
export function LetterHead({ page }: LayoutProps) {
  return (
    <Lines>
      <p className="date">{page.date}</p>
      <TextRegion page={page} part="recipient" />
      <Subject page={page} />
    </Lines>
  );
}

/** The salutation, the paragraphs, the closing and the name that signs. */
export function LetterText({ page, words }: LayoutProps & { words?: string }) {
  return (
    <Lines>
      <TextRegion page={page} part="salutation" />
      <Paragraphs page={page} />
      <TextRegion page={page} part="closing" />
      <p className="signature">
        <Words text={page.name} className={words} />
      </p>
    </Lines>
  );
}

/** The body of a letter as most templates print it: `<div class="letter-body">`. */
export function LetterBody({
  page,
  as: Body = "div",
  className = "letter-body",
  words,
}: LayoutProps & { as?: ElementType; className?: string; words?: string }) {
  return (
    <Body className={className}>
      <Lines>
        <LetterHead page={page} />
        <LetterText page={page} words={words} />
      </Lines>
    </Body>
  );
}
