/** academic: api/app/documents/templates/academic, the education before the experience, dates on the left. */
import type {
  SheetRole,
  SheetSchool,
} from "@/features/documents/helpers/sheet-page";
import {
  ExperienceBlock,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  RoleBullets,
  SchoolsBlock,
  SkillsBlock,
  StackedHeader,
  SummaryBlock,
  type LayoutProps,
  Lines,
} from "../parts";

function DatedSchool({ item }: { item: SheetSchool }) {
  return (
    <div className="dated school">
      <p className="when">{item.period}</p>
      <div className="what">
        <Lines>
          <h3 aria-level={4} className="entry-title">
            {item.degree}
          </h3>
          <p className="org">
            {item.school}
            {item.school && item.location ? ", " : ""}
            {item.location}
          </p>
        </Lines>
      </div>
    </div>
  );
}

function DatedRole({ role }: { role: SheetRole }) {
  return (
    <article className="dated entry">
      <Lines>
        <p className="when">{role.period}</p>
        <div className="what">
          <Lines>
            <h3 aria-level={4} className="entry-title">
              {role.title}
            </h3>
            <p className="org">
              {role.organisation}
              {role.organisation && role.location ? ", " : ""}
              {role.location}
            </p>
            <RoleBullets role={role} />
          </Lines>
        </div>
      </Lines>
    </article>
  );
}

export function AcademicCv({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} words="w" />
      <SummaryBlock page={page} wrap="text" />
      <SchoolsBlock
        page={page}
        wrap="text"
        school={(item) => <DatedSchool item={item} />}
      />
      <ExperienceBlock
        page={page}
        wrap="text"
        entry={(role) => <DatedRole role={role} />}
      />
      <ListBlock page={page} part="highlights" wrap="text" />
      <ListBlock page={page} part="competencies" wrap="text" />
      <SkillsBlock page={page} wrap="text" />
      <LanguagesBlock page={page} wrap="text" />
    </>
  );
}

export function AcademicLetter({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} words="w" />
      <LetterBody page={page} words="w" />
    </>
  );
}
