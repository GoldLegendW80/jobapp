import type { SectionName } from "./api";

export const SECTION_LABELS: Record<SectionName, string> = {
  identity: "Identity",
  experience: "Experience",
  facts: "Facts",
  skills: "Skills",
  search: "Search",
  answers: "Answers",
  preferences: "Preferences",
};

/** Names of the required fields the api lists as missing (schemas.py, REQUIRED). */
const FIELD_LABELS: Record<string, string> = {
  full_name: "full name",
  headline: "headline",
  email: "email",
  phone: "phone",
  city: "city",
  country: "country",
  languages: "at least one language",
  employers: "at least one employer",
  education: "at least one school",
  blocks: "at least one fact block",
  years_experience: "years of experience",
  have: "at least one skill category",
  title_include: "at least one title term",
  location: "at least one location term",
  contract_types: "at least one contract type",
  remote_stance: "remote stance",
  interactive_sites: "at least one job website ticked in Find jobs",
  work_authorization: "work authorisation for at least one country",
  notice_period: "notice period",
  earliest_start: "earliest start",
  salary_policy: "salary policy",
  motivation: "motivation",
};

export function missingLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replaceAll("_", " ");
}
