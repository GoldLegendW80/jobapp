import type { SkillList } from "./editor-forms";

/**
 * The words of each list of skills: its title, its name in an item's label, its help, and its
 * title in the fields of About you, which are the headings of the Skills part's text
 * (api/app/profile/about_text.py BUCKETS).
 */
export const SKILL_LIST_LABELS: Record<
  SkillList,
  { title: string; one: string; description: string; inWords: string }
> = {
  have: {
    title: "Competencies you have",
    one: "have",
    description: "A posting that asks for these raises the fit score.",
    inWords: "What I have",
  },
  adjacent: {
    title: "Adjacent competencies",
    one: "adjacent",
    description: "You partly cover these: they count for less.",
    inWords: "Close to what I have",
  },
  none: {
    title: "Competencies you lack",
    one: "lacking",
    description: "A posting that asks for these lowers the fit score.",
    inWords: "What I do not have",
  },
};

/** The requirements in the fields of About you, as the Skills part's text heads them (GATES). */
export const REQUIREMENTS_IN_WORDS = "Requirements that lower my fit";

/** Skills written in words (About you) or as regular expressions (Advanced matching). */
export type SkillMode = "words" | "regex";
