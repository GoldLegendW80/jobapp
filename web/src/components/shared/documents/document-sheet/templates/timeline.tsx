/** timeline: api/app/documents/templates/timeline, the roles along a thin vertical line. */
import {
  shows,
  type SheetSchool,
} from "@/features/documents/helpers/sheet-page";
import {
  BaseHeader,
  DatedEntry,
  ExperienceBlock,
  LanguagesBlock,
  LetterHead,
  LetterText,
  ListBlock,
  SchoolsBlock,
  SkillsBlock,
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
        <p className="org">{item.school}</p>
        {item.location ? <p className="place">{item.location}</p> : null}
      </Lines>
    </div>
  );
}

export function TimelineCv({ page }: LayoutProps) {
  return (
    <>
      <BaseHeader page={page} />
      <SummaryBlock page={page} />
      {shows(page, "highlights") || shows(page, "competencies") ? (
        <div className="about-cols">
          <Lines>
            <ListBlock page={page} part="highlights" />
            <ListBlock page={page} part="competencies" />
          </Lines>
        </div>
      ) : null}
      <ExperienceBlock
        page={page}
        wrap="rail"
        entry={(role) => <DatedEntry role={role} />}
      />
      <SchoolsBlock
        page={page}
        wrap="rail"
        school={(item) => <School item={item} />}
      />
      <SkillsBlock page={page} wrap="skills" />
      <LanguagesBlock page={page} />
    </>
  );
}

export function TimelineLetter({ page }: LayoutProps) {
  return (
    <>
      <BaseHeader page={page} />
      <div className="letter-body">
        <Lines>
          <LetterHead page={page} />
          <div className="letter-text">
            <Lines>
              <LetterText page={page} />
            </Lines>
          </div>
        </Lines>
      </div>
    </>
  );
}
