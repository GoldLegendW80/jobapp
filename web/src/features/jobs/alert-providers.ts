import type { AlertEmails } from "@/features/profile/api";

/**
 * The providers of the "Alert e-mails" group of Find jobs setup (SPEC-mail.md, "Job alert
 * e-mails: a narrow exception"): one per provider of api/app/mail/alerts/providers.py.
 */
export const ALERT_PROVIDERS = [
  {
    key: "indeed",
    label: "Indeed",
    howTo:
      "Create a daily job alert on fr.indeed.com for each title; the app reads only these alert e-mails.",
  },
  {
    key: "linkedin",
    label: "LinkedIn",
    howTo:
      "Turn on a job alert for each saved LinkedIn search; the app reads only these alert e-mails.",
  },
] as const satisfies readonly {
  key: keyof AlertEmails;
  label: string;
  howTo: string;
}[];

export const NO_ALERTS: AlertEmails = { indeed: false, linkedin: false };

/** The saved switches, every provider off when the search was saved before them. */
export function alertSettings(value: AlertEmails | undefined): AlertEmails {
  return { ...NO_ALERTS, ...(value ?? {}) };
}
