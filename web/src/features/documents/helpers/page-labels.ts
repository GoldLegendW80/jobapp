/**
 * What the document's page prints around the text the person writes, as the templates print it
 * (api/app/documents/templates/_base/labels.json): the headings in the page's language, the
 * periods of a role or a school, and the date of a letter.
 */
import type { Language } from "../api/documents-api";

export interface PageLabels {
  summary: string;
  highlights: string;
  competencies: string;
  experience: string;
  education: string;
  skills: string;
  languages: string;
  contact: string;
  subject: string;
  to: string;
  present: string;
  months: readonly string[];
}

export const PAGE_LABELS: Record<Language, PageLabels> = {
  en: {
    summary: "Profile",
    highlights: "Selected achievements",
    competencies: "Key competencies",
    experience: "Experience",
    education: "Education",
    skills: "Skills",
    languages: "Languages",
    contact: "Contact",
    subject: "Subject:",
    to: "to",
    present: "present",
    months: [
      "January",
      "February",
      "March",
      "April",
      "May",
      "June",
      "July",
      "August",
      "September",
      "October",
      "November",
      "December",
    ],
  },
  fr: {
    summary: "Profil",
    highlights: "Réalisations",
    competencies: "Compétences clés",
    experience: "Expérience",
    education: "Formation",
    skills: "Compétences techniques",
    languages: "Langues",
    contact: "Contact",
    subject: "Objet\u00a0:",
    to: "à",
    present: "aujourd'hui",
    months: [
      "janvier",
      "février",
      "mars",
      "avril",
      "mai",
      "juin",
      "juillet",
      "août",
      "septembre",
      "octobre",
      "novembre",
      "décembre",
    ],
  },
};

/** "2021" or "March 2021" from "2021" or "2021-03"; what is not a period prints as typed. */
function when(value: string, labels: PageLabels): string {
  const match = /^(\d{4})(?:-(\d{2}))?$/.exec(value.trim());
  if (!match) return value.trim();
  const month = match[2] === undefined ? undefined : Number(match[2]);
  const name = month === undefined ? undefined : labels.months[month - 1];
  return name === undefined ? (match[1] ?? "") : `${name} ${match[1] ?? ""}`;
}

/** "March 2021 to present", "2021 to 2023", "2021", as templates.format_period: a school (`ongoing` false) without an end prints its start alone. */
export function periodOf(
  start: string | null | undefined,
  end: string | null | undefined,
  labels: PageLabels,
  ongoing = true,
): string {
  const first = start ? when(start, labels) : "";
  const last = end ? when(end, labels) : "";
  if (first === "") return last;
  if (last === "")
    return ongoing ? `${first} ${labels.to} ${labels.present}` : first;
  return first === last ? first : `${first} ${labels.to} ${last}`;
}

/** Today as a letter dates itself: "6 November 2026", "1er novembre 2026". */
export function letterDate(language: Language, now: Date = new Date()): string {
  const labels = PAGE_LABELS[language];
  const day = now.getDate();
  const number = language === "fr" && day === 1 ? "1er" : String(day);
  return `${number} ${labels.months[now.getMonth()] ?? ""} ${String(now.getFullYear())}`;
}
