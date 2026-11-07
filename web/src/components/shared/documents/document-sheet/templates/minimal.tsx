/** minimal: api/app/documents/templates/minimal, headings and dates in a left margin. */
import type { SheetSchool } from "@/features/documents/helpers/sheet-page";
import {
  DatedEntry,
  ExperienceBlock,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  SchoolsBlock,
  SkillsBlock,
  StackedHeader,
  SummaryBlock,
  type LayoutProps,
  Lines,
} from "../parts";

function School({ item }: { item: SheetSchool }) {
  return (
    <div className="school">
      <Lines>
        <h3 aria-level={4} className="entry-title">
          {item.degree}
        </h3>
        {item.period ? <p className="period">{item.period}</p> : null}
        <p className="org">
          {item.school}
          {item.location ? `, ${item.location}` : ""}
        </p>
      </Lines>
    </div>
  );
}

export function MinimalCv({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <SummaryBlock page={page} wrap="body" />
      <ListBlock page={page} part="highlights" wrap="body" />
      <ListBlock page={page} part="competencies" wrap="body" />
      <ExperienceBlock
        page={page}
        section="block dated"
        wrap="body"
        entry={(role) => <DatedEntry role={role} />}
      />
      <SchoolsBlock
        page={page}
        section="block dated"
        wrap="body"
        school={(item) => <School item={item} />}
      />
      <SkillsBlock page={page} wrap="body" />
      <LanguagesBlock page={page} wrap="body" />
    </>
  );
}

export function MinimalLetter({ page }: LayoutProps) {
  return (
    <>
      <StackedHeader page={page} />
      <LetterBody page={page} />
    </>
  );
}
