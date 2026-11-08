import type { SkillList } from "./editor-forms";

/**
 * The words of each list of skills: its help, and its title in the fields of About you, which
 * are the headings of the Skills part's text (api/app/profile/about_text.py BUCKETS).
 */
export const SKILL_LIST_LABELS: Record<
  SkillList,
  { description: string; inWords: string }
> = {
  have: {
    description: "A posting that asks for these raises the fit score.",
    inWords: "What I have",
  },
  adjacent: {
    description: "You partly cover these: they count for less.",
    inWords: "Close to what I have",
  },
  none: {
    description: "A posting that asks for these lowers the fit score.",
    inWords: "What I do not have",
  },
};

/** The requirements in the fields of About you, as the Skills part's text heads them (GATES). */
export const REQUIREMENTS_IN_WORDS = "Requirements that lower my fit";
