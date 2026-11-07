/** banner: api/app/documents/templates/banner, a coloured band across the top. */
import {
  BaseHeader,
  ExperienceBlock,
  LanguagesBlock,
  LetterBody,
  ListBlock,
  SchoolsBlock,
  SkillsBlock,
  SummaryBlock,
  type LayoutProps,
  Lines,
} from "../parts";

export function BannerCv({ page }: LayoutProps) {
  return (
    <>
      <div className="band">
        <Lines>
          <BaseHeader page={page} />
        </Lines>
      </div>
      <div className="content">
        <Lines>
          <SummaryBlock page={page} />
          <ListBlock page={page} part="highlights" />
          <ListBlock page={page} part="competencies" />
          <ExperienceBlock page={page} />
          <SchoolsBlock page={page} wrap="schools" />
          <SkillsBlock page={page} wrap="skills" />
          <LanguagesBlock page={page} />
        </Lines>
      </div>
    </>
  );
}

export function BannerLetter({ page }: LayoutProps) {
  return (
    <>
      <div className="band">
        <Lines>
          <BaseHeader page={page} />
        </Lines>
      </div>
      <LetterBody page={page} className="content letter-body" />
    </>
  );
}
