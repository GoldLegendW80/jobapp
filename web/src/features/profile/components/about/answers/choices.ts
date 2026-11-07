/** A value of a choice of the answers form, and its words. */
export interface Choice {
  value: string;
  label: string;
}

/** Yes, no, or not said: the three answers of an optional yes or no question. */
export const YES_NO_NOT_SAID: readonly Choice[] = [
  { value: "", label: "Not said" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

export const STANCES: readonly Choice[] = [
  { value: "", label: "Automatic" },
  { value: "leave_blank", label: "Leave it blank" },
  { value: "range", label: "Give my range" },
  { value: "sentence", label: "Write my salary policy" },
];

export const PERIODS: readonly Choice[] = [
  { value: "year", label: "Year" },
  { value: "month", label: "Month" },
  { value: "day", label: "Day" },
];

export const POLICIES: readonly Choice[] = [
  { value: "decline", label: "Decline to answer" },
  { value: "leave", label: "Leave them to me" },
  { value: "stored", label: "Use the answers I store here" },
];

/** The words of `value` among `choices`. */
export const labelOf = (choices: readonly Choice[], value: string) =>
  choices.find((choice) => choice.value === value)?.label ?? value;

/** How many of `values` hold something. */
export const filled = (values: readonly unknown[]) =>
  values.filter((value) => typeof value === "string" && value.trim() !== "")
    .length;

/**
 * The fields of each group of the answers form, to open the groups an error is in. The keys
 * are the groups' values in the accordion.
 */
export const GROUP_FIELDS = {
  authorization: ["work_authorization"],
  availability: [
    "notice_period",
    "earliest_start",
    "salary_policy",
    "relocation",
    "travel",
  ],
  "on-forms": [
    "preferred_name",
    "pronouns",
    "years_experience_stated",
    "highest_degree",
    "highest_degree_year",
  ],
  setup: [
    "willing_onsite",
    "willing_hybrid",
    "previously_applied_default",
    "background_check_consent",
    "reference_check_consent",
  ],
  salary: ["salary_stance", "salary_ranges"],
  eeo: ["eeo_policy", "eeo_answers"],
  "own-words": ["motivation", "heard_about"],
  "leave-blank": ["leave_blank"],
} as const;
