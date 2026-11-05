/** "Ready for" of the one page profile (analysis B7): what each use of the profile still lacks,
 * from the completeness the api computes, and the one button that fixes it. */
import type { Profile, SectionName } from "./api";

export interface Readiness {
  name: string;
  ready: boolean;
  /** the one button that fixes it */
  fix?: { label: string; href: string };
}

/** What each use of the profile still lacks, from the completeness the api computes. */
export function readiness(profile: Profile): Readiness[] {
  const missing = (section: SectionName) =>
    profile.completeness.sections[section]?.missing ?? [];
  const first = (...fixes: [boolean, string, string][]) => {
    const found = fixes.find(([lacking]) => lacking);
    return found ? { label: found[1], href: found[2] } : undefined;
  };
  const scoring = first(
    [missing("skills").length > 0, "Add your skills", "#about-skills"],
    [
      missing("search").includes("title_include"),
      "Add a job title",
      "#looking-for",
    ],
  );
  const writing = first(
    [
      missing("experience").length > 0,
      "Add your experience",
      "#about-experience",
    ],
    [missing("facts").length > 0, "Add facts about your work", "#about-you"],
  );
  const filling = first(
    [missing("identity").length > 0, "Add your contact details", "#contact"],
    [missing("answers").length > 0, "Add your answers", "#about-answers"],
  );
  return [
    { name: "Scoring postings", ready: !scoring, fix: scoring },
    { name: "Writing CVs", ready: !writing, fix: writing },
    { name: "Filling forms", ready: !filling, fix: filling },
  ];
}
