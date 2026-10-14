/**
 * Form values, zod schemas and conversions of the three editable sections (identity, experience,
 * answers). The schemas mirror the api's models (api/app/profile/schemas.py) so most mistakes are
 * caught before a save: lengths, required fields, dates written YYYY or YYYY-MM between 1900 and
 * 2100 (period.ts), end not certainly before start, unique employer keys, languages and countries,
 * full http(s) addresses, emails and phone numbers. The api stays the judge: its 422 lands on the
 * field it names (errors.ts).
 *
 * In a form every text is a string ("" for a value the api stores as null); `to*Form` and
 * `from*Form` convert, trimming and turning "" back into null.
 */
import { z } from "zod";

import {
  EEO_POLICIES,
  EEO_TOPICS,
  EMPLOYER_CONTRACTS,
  SALARY_PERIODS,
  SALARY_STANCES,
  type Answers,
  type Employer,
  type Experience,
  type Identity,
  type School,
} from "./api";
import { endsBeforeStart, isPeriod } from "./period";

export const LINE_MAX = 200;
export const TEXT_MAX = 2000;
export const LONG_MAX = 10_000;
export const URL_MAX = 500;
export const EMAIL_MAX = 320;
export const PHONE_MAX = 40;
export const KEY_MAX = 40;
export const LINKS_MAX = 10;
export const LANGUAGES_MAX = 20;
export const EMPLOYERS_MAX = 30;
export const EDUCATION_MAX = 20;
export const AUTHORIZATIONS_MAX = 50;
export const LEAVE_BLANK_MAX = 50;
export const SALARY_RANGES_MAX = 10;

// C0 and C1 control characters and DEL; multi-line text keeps newline and tab.
// eslint-disable-next-line no-control-regex
const CONTROL_LINE = /[\u0000-\u001f\u007f-\u009f]/;
// eslint-disable-next-line no-control-regex
const CONTROL_TEXT = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/;

export const KEY_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const URL_PATTERN = /^https?:\/\/[^\s/?#]+\.[^\s/?#]+(?:[/?#]\S*)?$/i;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_PATTERN = /^\+?[0-9][0-9 ().-]{4,38}$/;

/** A text field: trimmed length at most `max`, no control characters, required or not. */
export function text(
  label: string,
  max: number,
  { required = false, multiline = false } = {},
) {
  return z
    .string()
    .refine((v) => !required || v.trim() !== "", `Enter ${label}.`)
    .refine(
      (v) => v.trim().length <= max,
      `Use at most ${max.toLocaleString("en")} characters.`,
    )
    .refine(
      (v) => !(multiline ? CONTROL_TEXT : CONTROL_LINE).test(v),
      "Remove the control characters.",
    );
}

function optionalPattern(
  schema: z.ZodString,
  pattern: RegExp,
  message: string,
) {
  return schema.refine(
    (v) => v.trim() === "" || pattern.test(v.trim()),
    message,
  );
}

const url = (label: string, required = false) =>
  optionalPattern(
    text(label, URL_MAX, { required }),
    URL_PATTERN,
    "Enter a full address starting with http:// or https://.",
  );

const PERIOD_MESSAGE =
  "Write the date as YYYY or YYYY-MM, between 1900 and 2100.";

/**
 * A date field: "" (when optional), a month YYYY-MM or a year alone YYYY, between 1900 and 2100.
 * What was typed is what is saved: a year alone is never completed with a month.
 */
const period = (required: boolean) =>
  z
    .string()
    .refine(
      (v) => !required || v.trim() !== "",
      "Enter the start, as YYYY or YYYY-MM.",
    )
    .refine((v) => v.trim() === "" || isPeriod(v.trim()), PERIOD_MESSAGE);

/** Add an issue on `path` of each item whose `key(item)` repeats an earlier one. */
export function uniqueBy<T>(
  items: T[],
  key: (item: T) => string,
  field: string,
  message: string,
  ctx: z.RefinementCtx,
  list: string,
) {
  const seen = new Set<string>();
  items.forEach((item, index) => {
    const k = key(item);
    if (k === "") return;
    if (seen.has(k)) {
      ctx.addIssue({ code: "custom", message, path: [list, index, field] });
    }
    seen.add(k);
  });
}

export const fold = (value: string) =>
  value.trim().normalize("NFC").toLocaleLowerCase("en");
const orNull = (value: string) => (value.trim() === "" ? null : value.trim());
const textOrNull = (value: string) => {
  const cleaned = value.replace(/\r\n?/g, "\n").trim();
  return cleaned === "" ? null : cleaned;
};

// --- identity --------------------------------------------------------------------------------

export const identitySchema = z
  .object({
    full_name: text("your full name", LINE_MAX),
    headline: text("a headline", LINE_MAX),
    email: optionalPattern(
      text("an email", EMAIL_MAX),
      EMAIL_PATTERN,
      "Enter a valid email address.",
    ),
    phone: optionalPattern(
      text("a phone number", PHONE_MAX),
      PHONE_PATTERN,
      "Enter a phone number: digits, spaces, dots and brackets, optionally starting with +.",
    ),
    city: text("a city", LINE_MAX),
    country: text("a country", LINE_MAX),
    linkedin_url: url("a LinkedIn address"),
    website: url("a website"),
    links: z
      .array(
        z.object({
          label: text("a label", LINE_MAX, { required: true }),
          url: url("an address", true),
        }),
      )
      .max(LINKS_MAX, `Keep at most ${LINKS_MAX} links.`),
    languages: z
      .array(
        z.object({
          name: text("the language", LINE_MAX, { required: true }),
          level: text("a level", LINE_MAX),
        }),
      )
      .max(LANGUAGES_MAX, `Keep at most ${LANGUAGES_MAX} languages.`),
  })
  .superRefine((values, ctx) => {
    uniqueBy(
      values.languages,
      (l) => fold(l.name),
      "name",
      "This language is already listed.",
      ctx,
      "languages",
    );
  });
export type IdentityForm = z.infer<typeof identitySchema>;

export function toIdentityForm(identity: Identity): IdentityForm {
  return {
    full_name: identity.full_name ?? "",
    headline: identity.headline ?? "",
    email: identity.email ?? "",
    phone: identity.phone ?? "",
    city: identity.city ?? "",
    country: identity.country ?? "",
    linkedin_url: identity.linkedin_url ?? "",
    website: identity.website ?? "",
    links: identity.links.map((l) => ({ label: l.label, url: l.url })),
    languages: identity.languages.map((l) => ({
      name: l.name,
      level: l.level ?? "",
    })),
  };
}

export function fromIdentityForm(values: IdentityForm): Identity {
  return {
    full_name: orNull(values.full_name),
    headline: orNull(values.headline),
    email: orNull(values.email),
    phone: orNull(values.phone),
    city: orNull(values.city),
    country: orNull(values.country),
    linkedin_url: orNull(values.linkedin_url),
    website: orNull(values.website),
    links: values.links.map((l) => ({
      label: l.label.trim(),
      url: l.url.trim(),
    })),
    languages: values.languages.map((l) => ({
      name: l.name.trim(),
      level: orNull(l.level),
    })),
  };
}

// --- experience ------------------------------------------------------------------------------

/** A stable key from an organisation name: lowercase ASCII words joined by hyphens. */
export function slugify(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, KEY_MAX)
    .replace(/-+$/g, "");
}

const contractSchema = z.enum(["", ...EMPLOYER_CONTRACTS]);

const employerSchema = z
  .object({
    key: z
      .string()
      .refine((v) => v.trim() !== "", "Enter a key.")
      .refine(
        (v) => v.trim().length <= KEY_MAX,
        `Use at most ${KEY_MAX} characters.`,
      )
      .refine(
        (v) => v.trim() === "" || KEY_PATTERN.test(v.trim()),
        "Use lowercase letters and digits, words joined by single hyphens.",
      ),
    // true while the key follows the organisation; editing the key by hand stops it.
    autoKey: z.boolean(),
    organisation: text("the organisation", LINE_MAX, { required: true }),
    title: text("the job title", LINE_MAX, { required: true }),
    start: period(true),
    end: period(false),
    present: z.boolean(),
    location: text("a location", LINE_MAX),
    contract: contractSchema,
  })
  .superRefine((value, ctx) => {
    if (!value.present && value.end.trim() === "") {
      ctx.addIssue({
        code: "custom",
        message: "Enter the end, or tick Present.",
        path: ["end"],
      });
    }
    endAfterStart(value.start, value.present ? "" : value.end, ctx);
  });

/**
 * Periods, not text ("2023" sorts before "2023-05" as text), and only what is certain: a start
 * of 2023-05 with an end of 2023 is in order, the end may be December.
 */
function endAfterStart(start: string, end: string, ctx: z.RefinementCtx) {
  if (endsBeforeStart(start.trim(), end.trim())) {
    ctx.addIssue({
      code: "custom",
      message: "The end is before the start.",
      path: ["end"],
    });
  }
}

const schoolSchema = z
  .object({
    school: text("the school", LINE_MAX, { required: true }),
    degree: text("the degree", LINE_MAX, { required: true }),
    start: period(false),
    end: period(false),
    location: text("a location", LINE_MAX),
    details: text("details", TEXT_MAX, { multiline: true }),
  })
  .superRefine((value, ctx) => {
    endAfterStart(value.start, value.end, ctx);
  });

export const experienceSchema = z
  .object({
    employers: z
      .array(employerSchema)
      .max(EMPLOYERS_MAX, `Keep at most ${EMPLOYERS_MAX} employers.`),
    education: z
      .array(schoolSchema)
      .max(EDUCATION_MAX, `Keep at most ${EDUCATION_MAX} schools.`),
  })
  .superRefine((values, ctx) => {
    uniqueBy(
      values.employers,
      (e) => e.key.trim(),
      "key",
      "Another employer already uses this key.",
      ctx,
      "employers",
    );
  });
export type ExperienceForm = z.infer<typeof experienceSchema>;
export type EmployerForm = ExperienceForm["employers"][number];
export type SchoolForm = ExperienceForm["education"][number];

export const NEW_EMPLOYER: EmployerForm = {
  key: "",
  autoKey: true,
  organisation: "",
  title: "",
  start: "",
  end: "",
  present: false,
  location: "",
  contract: "",
};
export const NEW_SCHOOL: SchoolForm = {
  school: "",
  degree: "",
  start: "",
  end: "",
  location: "",
  details: "",
};

export function toExperienceForm(experience: Experience): ExperienceForm {
  return {
    employers: experience.employers.map((e) => ({
      key: e.key,
      autoKey: false,
      organisation: e.organisation,
      title: e.title,
      start: e.start,
      end: e.end ?? "",
      present: e.end === null,
      location: e.location ?? "",
      contract: e.contract ?? "",
    })),
    education: experience.education.map((s) => ({
      school: s.school,
      degree: s.degree,
      start: s.start ?? "",
      end: s.end ?? "",
      location: s.location ?? "",
      details: s.details ?? "",
    })),
  };
}

export function fromExperienceForm(values: ExperienceForm): Experience {
  return {
    employers: values.employers.map((e): Employer => ({
      key: e.key.trim(),
      organisation: e.organisation.trim(),
      title: e.title.trim(),
      start: e.start.trim(),
      end: e.present ? null : orNull(e.end),
      location: orNull(e.location),
      contract: e.contract === "" ? null : e.contract,
    })),
    education: values.education.map((s): School => ({
      school: s.school.trim(),
      degree: s.degree.trim(),
      start: orNull(s.start),
      end: orNull(s.end),
      location: orNull(s.location),
      details: textOrNull(s.details),
    })),
  };
}

// --- answers ---------------------------------------------------------------------------------

const sponsorshipSchema = z.enum(["", "yes", "no"]);
/** Yes, no, or not said: the form value of an optional boolean. */
const triState = z.enum(["", "yes", "no"]);
const WHOLE_NUMBER = /^\d{1,9}$/;
const wholeNumber = (label: string, required: boolean) =>
  z
    .string()
    .refine((v) => v.trim() !== "" || !required, `Enter ${label}.`)
    .refine(
      (v) => v.trim() === "" || WHOLE_NUMBER.test(v.trim()),
      `Enter ${label} as a whole number.`,
    );

export const answersSchema = z
  .object({
    work_authorization: z
      .array(
        z.object({
          country: text("the country", LINE_MAX, { required: true }),
          answer: text("the answer", TEXT_MAX, {
            required: true,
            multiline: true,
          }),
          needs_sponsorship: sponsorshipSchema,
        }),
      )
      .max(AUTHORIZATIONS_MAX, `Keep at most ${AUTHORIZATIONS_MAX} countries.`),
    notice_period: text("the notice period", TEXT_MAX, { multiline: true }),
    earliest_start: text("the earliest start", LINE_MAX),
    salary_policy: text("the salary policy", TEXT_MAX, { multiline: true }),
    relocation: text("relocation", TEXT_MAX, { multiline: true }),
    travel: text("travel", TEXT_MAX, { multiline: true }),
    motivation: text("your motivation", LONG_MAX, { multiline: true }),
    heard_about: text("how you heard", TEXT_MAX, { multiline: true }),
    leave_blank: z
      .array(
        z.object({ value: text("a question", LINE_MAX, { required: true }) }),
      )
      .max(LEAVE_BLANK_MAX, `Keep at most ${LEAVE_BLANK_MAX} entries.`),
    preferred_name: text("a preferred name", LINE_MAX),
    pronouns: text("your pronouns", LINE_MAX),
    years_experience_stated: text("your years of experience", LINE_MAX),
    highest_degree: text("your highest degree", LINE_MAX),
    highest_degree_year: z
      .string()
      .refine(
        (v) =>
          v.trim() === "" ||
          (/^\d{4}$/.test(v.trim()) && Number(v) >= 1950 && Number(v) <= 2100),
        "Enter a year between 1950 and 2100.",
      ),
    willing_onsite: triState,
    willing_hybrid: triState,
    previously_applied_default: triState,
    background_check_consent: triState,
    reference_check_consent: triState,
    eeo_policy: z.enum(EEO_POLICIES),
    eeo_answers: z
      .array(
        z.object({
          topic: z.enum(EEO_TOPICS),
          answer: text("the answer", LINE_MAX, { required: true }),
        }),
      )
      .max(EEO_TOPICS.length, `Keep at most ${EEO_TOPICS.length} answers.`),
    salary_stance: z.enum(["", ...SALARY_STANCES]),
    salary_ranges: z
      .array(
        z
          .object({
            currency: z
              .string()
              .refine(
                (v) => /^[A-Z]{3}$/.test(v.trim()),
                "Enter a currency code of three capital letters, such as EUR.",
              ),
            minimum: wholeNumber("the minimum", true),
            maximum: wholeNumber("the maximum", false),
            period: z.enum(SALARY_PERIODS),
          })
          .superRefine((range, ctx) => {
            const { minimum, maximum } = range;
            if (
              WHOLE_NUMBER.test(minimum.trim()) &&
              WHOLE_NUMBER.test(maximum.trim()) &&
              Number(maximum) < Number(minimum)
            ) {
              ctx.addIssue({
                code: "custom",
                message: "The maximum is below the minimum.",
                path: ["maximum"],
              });
            }
          }),
      )
      .max(SALARY_RANGES_MAX, `Keep at most ${SALARY_RANGES_MAX} ranges.`),
  })
  .superRefine((values, ctx) => {
    uniqueBy(
      values.work_authorization,
      (w) => fold(w.country),
      "country",
      "This country is already listed.",
      ctx,
      "work_authorization",
    );
    uniqueBy(
      values.eeo_answers,
      (a) => a.topic,
      "topic",
      "This topic is already listed.",
      ctx,
      "eeo_answers",
    );
    uniqueBy(
      values.salary_ranges,
      (r) => r.currency.trim(),
      "currency",
      "This currency is already listed.",
      ctx,
      "salary_ranges",
    );
  });
export type AnswersForm = z.infer<typeof answersSchema>;

export function toAnswersForm(answers: Answers): AnswersForm {
  return {
    work_authorization: answers.work_authorization.map((w) => ({
      country: w.country,
      answer: w.answer,
      needs_sponsorship:
        w.needs_sponsorship === null ? "" : w.needs_sponsorship ? "yes" : "no",
    })),
    notice_period: answers.notice_period ?? "",
    earliest_start: answers.earliest_start ?? "",
    salary_policy: answers.salary_policy ?? "",
    relocation: answers.relocation ?? "",
    travel: answers.travel ?? "",
    motivation: answers.motivation ?? "",
    heard_about: answers.heard_about ?? "",
    leave_blank: answers.leave_blank.map((value) => ({ value })),
    preferred_name: answers.preferred_name ?? "",
    pronouns: answers.pronouns ?? "",
    years_experience_stated: answers.years_experience_stated ?? "",
    highest_degree: answers.highest_degree ?? "",
    highest_degree_year:
      answers.highest_degree_year === null
        ? ""
        : String(answers.highest_degree_year),
    willing_onsite: fromBoolean(answers.willing_onsite),
    willing_hybrid: fromBoolean(answers.willing_hybrid),
    previously_applied_default: fromBoolean(answers.previously_applied_default),
    background_check_consent: fromBoolean(answers.background_check_consent),
    reference_check_consent: fromBoolean(answers.reference_check_consent),
    eeo_policy: answers.eeo_policy,
    eeo_answers: answers.eeo_answers.map((a) => ({ ...a })),
    salary_stance: answers.salary_stance ?? "",
    salary_ranges: answers.salary_ranges.map((r) => ({
      currency: r.currency,
      minimum: String(r.minimum),
      maximum: r.maximum === null ? "" : String(r.maximum),
      period: r.period,
    })),
  };
}

function fromBoolean(value: boolean | null): "" | "yes" | "no" {
  return value === null ? "" : value ? "yes" : "no";
}

function toBoolean(value: "" | "yes" | "no"): boolean | null {
  return value === "" ? null : value === "yes";
}

export function fromAnswersForm(values: AnswersForm): Answers {
  return {
    work_authorization: values.work_authorization.map((w) => ({
      country: w.country.trim(),
      answer: w.answer.replace(/\r\n?/g, "\n").trim(),
      needs_sponsorship:
        w.needs_sponsorship === "" ? null : w.needs_sponsorship === "yes",
    })),
    notice_period: textOrNull(values.notice_period),
    earliest_start: orNull(values.earliest_start),
    salary_policy: textOrNull(values.salary_policy),
    relocation: textOrNull(values.relocation),
    travel: textOrNull(values.travel),
    motivation: textOrNull(values.motivation),
    heard_about: textOrNull(values.heard_about),
    leave_blank: values.leave_blank.map((l) => l.value.trim()),
    preferred_name: orNull(values.preferred_name),
    pronouns: orNull(values.pronouns),
    years_experience_stated: orNull(values.years_experience_stated),
    highest_degree: orNull(values.highest_degree),
    highest_degree_year:
      values.highest_degree_year.trim() === ""
        ? null
        : Number(values.highest_degree_year),
    willing_onsite: toBoolean(values.willing_onsite),
    willing_hybrid: toBoolean(values.willing_hybrid),
    previously_applied_default: toBoolean(values.previously_applied_default),
    background_check_consent: toBoolean(values.background_check_consent),
    reference_check_consent: toBoolean(values.reference_check_consent),
    eeo_policy: values.eeo_policy,
    eeo_answers: values.eeo_answers.map((a) => ({
      topic: a.topic,
      answer: a.answer.trim(),
    })),
    salary_stance: values.salary_stance === "" ? null : values.salary_stance,
    salary_ranges: values.salary_ranges.map((r) => ({
      currency: r.currency.trim(),
      minimum: Number(r.minimum),
      maximum: r.maximum.trim() === "" ? null : Number(r.maximum),
      period: r.period,
    })),
  };
}
