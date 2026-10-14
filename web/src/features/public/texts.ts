/**
 * The words of /privacy, /terms and the public home page, in one module. Every sentence of the
 * privacy page is a named constant here, copied from tasks/mail/todo.md (M2) and tested against
 * the code by M21: a sentence changes with the code it describes, in the task that changes the
 * code, and `VERSION` changes with it. M4 records `VERSION` with the consent of the user.
 *
 * Every figure is a constant of its own, interpolated once, so no number is typed twice.
 */

/** The version of the texts below. Bump it whenever a sentence of /privacy or /terms changes. */
export const VERSION = "2026-10-14";

/** The first sync searches this many days back (MAIL first sync, `newer_than:180d`). */
export const FIRST_SYNC_DAYS = 180;
/** The log of sends outlives a disconnect this many days (`MAIL_SENDS_KEEP_DAYS`). */
export const SENDS_KEEP_DAYS = 365;
/** Deleted mail leaves the server's backups within this many days (platform T10). */
export const BACKUP_DAYS = 7;
/** The storage ceiling per user (decision 14: `MAIL_STORAGE_MAX_MESSAGES`, `MAIL_STORAGE_MAX_BYTES`). */
export const STORAGE_MAX_MESSAGES = 20_000;
export const STORAGE_MAX_MEGABYTES = 300;

/**
 * Where the administrator is reached, set at build time with `VITE_CONTACT_EMAIL`. No address is
 * published until the owner has confirmed which one to show: unset or blank gives an empty string.
 * A function and not a constant, so that a test can stub the value.
 */
export function contactEmail(): string {
  const value: unknown = import.meta.env.VITE_CONTACT_EMAIL;
  return typeof value === "string" ? value.trim() : "";
}

/** The Contact section says this while no address is configured. */
export const NO_CONTACT = "No contact address is published yet.";

/**
 * The terms of the first sync's search. Gmail is asked for mail that contains one of the
 * keywords, or that comes from or goes to one of the systems, or to a company the user has a
 * posting for. M7's `query.py` builds the query from the same lists: the two must agree.
 */
export const SEARCH_KEYWORDS = [
  "application",
  "candidature",
  "interview",
  "entretien",
] as const;
export const SEARCH_SYSTEMS = [
  "ashbyhq.com",
  "greenhouse.io",
  "lever.co",
  "workday",
  "smartrecruiters",
  "teamtailor",
  "welcometothejungle",
] as const;
export const SEARCH_COMPANIES =
  "The websites of the companies you have a posting for in the app.";

export const WHAT_IS_READ =
  "The app looks at the sender, the recipients, the subject and the short preview of each new mail to decide whether it concerns one of your job postings. " +
  "It reads and stores the whole mail only when it does. " +
  `When you connect Gmail, it asks Gmail to search your last ${FIRST_SYNC_DAYS} days for the terms listed below. ` +
  "That search can also catch mail that is not about a job.";

export const WHERE_STORED = "This instance, on the owner's server.";

export const ASSISTANT =
  "Message content is sent to your own model provider only to match, label and draft, and only after you have given your consent; without it nothing of a mail is sent to a model. " +
  "Nothing is shared with anyone else.";

export const HOW_LONG =
  "Stored mail is kept until you disconnect or delete a thread, within a ceiling of " +
  `${STORAGE_MAX_MESSAGES.toLocaleString("en-US")} messages or ${STORAGE_MAX_MEGABYTES} MB of mail text, ` +
  `and it leaves the server's backups within ${BACKUP_DAYS} days.`;

export const AFTER_DISCONNECT =
  `After you disconnect, the app keeps for ${SENDS_KEEP_DAYS} days a log of the mails you sent through it, without any address or text, ` +
  "and keeps in the history of each posting the fact that its status changed because of a mail.";

export const DISABLED_ACCOUNT =
  "If the administrator disables your account, the app stops reading your mailbox and keeps what it stored until the account is enabled again or deleted.";

export const DELETE_ACCOUNT =
  "The app has no button to delete an account. " +
  "Write to the administrator at the address below: he deletes your account and everything in it, and the access is removed at Google.";

export const DISCONNECT =
  "To disconnect, open Account, find the Gmail card and choose Disconnect. " +
  "The app revokes its access at Google and deletes the mail it stored for you.";

export const LIMITED_USE =
  "The use of information received from Google APIs will adhere to the Google API Services User Data Policy, including the Limited Use requirements.";

export const GOOGLE_POLICY_URL =
  "https://developers.google.com/terms/api-services-user-data-policy";

/** /terms: a short plain statement. */
export const TERMS_PRIVATE =
  "jobapp is a private tool, open by invitation only.";
export const TERMS_WARRANTY =
  "It is provided as it is, with no warranty of any kind.";
export const TERMS_RESPONSIBILITY =
  "You are responsible for what you send through it.";

/** The public home page. */
export const HOME_INTRO =
  "jobapp is a private tool to follow a job search: the postings it finds and tracks, your profile and, when you connect Gmail, the mail that answers them.";
export const HOME_INVITE =
  "It is open by invitation only. If you have an account, sign in.";
