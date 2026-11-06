import type { SkillList } from "./editor-forms";

/** The words of each list of the Skills tab: its title, its name in an item's label, its help. */
export const SKILL_LIST_LABELS: Record<
  SkillList,
  { title: string; one: string; description: string }
> = {
  have: {
    title: "Competencies you have",
    one: "have",
    description:
      "A posting that asks for these raises the fit score. Each category is a name and the words that reveal it.",
  },
  adjacent: {
    title: "Adjacent competencies",
    one: "adjacent",
    description: "You partly cover these: they count for less.",
  },
  none: {
    title: "Competencies you lack",
    one: "lacking",
    description: "A posting that asks for these lowers the fit score.",
  },
};
