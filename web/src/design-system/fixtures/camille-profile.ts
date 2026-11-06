import type { User } from "@/features/auth/api/auth-api";
import type { SectionData } from "@/features/profile/api/profile-api";
import type { TitleSuggestions } from "@/features/profile/api/title-suggestions-api";

/** The invented person of the profile stories: never a real person, an @example.org address. */
export const camille: User = {
  id: "00000000-0000-4000-8000-0000000000c1",
  email: "camille@example.org",
  name: "Camille Martin",
  role: "member",
  created_at: "2026-10-08T10:00:00Z",
  last_login_at: "2026-11-05T08:30:00Z",
};

const WEEK = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
] as const;

/** Camille's whole profile: a data engineer in Lyon, seven years in. */
export function camilleProfile(): SectionData {
  return {
    identity: {
      full_name: "Camille Martin",
      headline: "Senior data engineer",
      email: "camille@example.org",
      phone: "+33 6 00 00 00 00",
      city: "Lyon",
      country: "France",
      linkedin_url: "https://www.linkedin.com/in/example/",
      website: "https://example.org/",
      links: [
        { label: "github.com/example", url: "https://github.com/example" },
      ],
      languages: [
        { name: "French", level: "Mother tongue" },
        { name: "English", level: "Advanced (C1)" },
      ],
    },
    experience: {
      employers: [
        {
          key: "lumen-freight",
          organisation: "Lumen Freight",
          title: "Senior data engineer",
          start: "2022-04",
          end: null,
          location: "Lyon",
          contract: "CDI",
        },
        {
          key: "vellum-analytics",
          organisation: "Vellum Analytics",
          title: "Data engineer",
          start: "2019-09",
          end: "2022-03",
          location: "Lyon",
          contract: "CDI",
        },
        {
          key: "cartopia",
          organisation: "Cartopia",
          title: "Data analyst (apprenticeship)",
          start: "2017-09",
          end: "2019-08",
          location: "Villeurbanne",
          contract: "apprenticeship",
        },
      ],
      education: [
        {
          school: "Institut Polytechnique de Fourvière",
          degree: "Master of Engineering, data science",
          start: "2014-09",
          end: "2019-06",
          location: "Lyon",
          details: "Thesis on streaming joins at scale",
        },
      ],
    },
    facts: {
      blocks: [
        {
          title: "How I work",
          body: "I like small teams that own their data from the source to the dashboard. I write tests for every pipeline and document each table I ship.",
        },
        {
          title: "Projects",
          body: "- Real time parcel tracking on Kafka, 2024: delays seen in seconds, not hours.\n- Migration of 140 models to dbt, 2022: the nightly build went from 3 hours to 25 minutes.",
        },
      ],
    },
    skills: {
      years_experience: 7,
      have: [
        {
          name: "data_pipelines",
          advanced_regex: false,
          terms: ["python", "sql", "dbt", "airflow"],
        },
        {
          name: "streaming",
          advanced_regex: false,
          terms: ["kafka", "flink"],
        },
        {
          name: "cloud",
          advanced_regex: false,
          terms: ["bigquery", "gcp", "terraform"],
        },
      ],
      adjacent: [
        {
          name: "machine_learning",
          advanced_regex: false,
          terms: ["mlflow", "feature store"],
        },
      ],
      none: [
        {
          name: "frontend",
          advanced_regex: false,
          terms: ["react", "angular"],
        },
      ],
      gates: [
        {
          name: "german",
          advanced_regex: false,
          terms: ["fluent german"],
          penalty: 20,
        },
      ],
    },
    search: {
      title_include: {
        advanced_regex: false,
        terms: ["data engineer", "analytics engineer", "data platform"],
      },
      title_exclude: { advanced_regex: false, terms: ["intern", "stage"] },
      location: { advanced_regex: false, terms: ["Lyon", "Remote"] },
      location_exclude: { advanced_regex: false, terms: [] },
      contract_types: ["CDI"],
      remote_stance: "hybrid",
      min_fit: 70,
      days: 30,
      never_apply: ["No defence or weapons companies."],
      sweep_schedule: {
        enabled: true,
        days: [...WEEK],
        times: ["07:30"],
        timezone: "Europe/Paris",
      },
      documents: { auto: true, min_fit: 75, daily_ceiling: 10 },
      seniority: ["senior"],
      industries: { advanced_regex: false, terms: ["logistics", "health"] },
      company_types: ["scaleup", "startup"],
      compensation: {
        salary: { min: 55000, max: 70000, currency: "EUR" },
        daily_rate: null,
      },
    },
    answers: {
      work_authorization: [
        {
          country: "France",
          answer: "French citizen, no sponsorship needed.",
          needs_sponsorship: false,
        },
      ],
      notice_period: "Three months.",
      earliest_start: "February 2027",
      salary_policy: "Give the range of the posting when there is one.",
      relocation: "Lyon or remote only.",
      travel: "Up to two days a month.",
      motivation:
        "I build data platforms people trust, and I want to do it for a product that moves real things.",
      heard_about: "The company's careers page.",
      leave_blank: [],
      preferred_name: null,
      pronouns: null,
      years_experience_stated: "7 years",
      highest_degree: "Master's degree",
      highest_degree_year: 2019,
      willing_onsite: true,
      willing_hybrid: true,
      previously_applied_default: false,
      background_check_consent: true,
      reference_check_consent: true,
      eeo_policy: "decline",
      eeo_answers: [],
      salary_stance: "range",
      salary_ranges: [
        { currency: "EUR", minimum: 55000, maximum: 70000, period: "year" },
      ],
    },
    preferences: {
      onboarding: {
        completed_at: "2026-10-08T10:30:00Z",
        dismissed_at: null,
        steps_passed: [],
      },
      documents_language: "auto",
      documents_tone: "plain",
    },
  };
}

/** Camille's "About you" document, as she wrote it. */
export const CAMILLE_ABOUT = `## Skills

Years of experience: 7

### What I have
- data_pipelines: Python, SQL, dbt, Airflow
- streaming: Kafka, Flink
- cloud: BigQuery, GCP, Terraform

### Close to what I have
- machine_learning: MLflow, feature stores

### What I do not do
- frontend: React, Angular

## Answers

### Notice period
Three months.

### Earliest start
February 2027.

### Work authorisation
France: French citizen, no sponsorship needed.

### Salary
Between 55,000 and 70,000 EUR a year.

## How I work

I like small teams that own their data from the source to the dashboard. I write **tests for every pipeline** and document each table I ship.

## Projects

| Project | Year | What it changed |
|---|---|---|
| Real time parcel tracking on Kafka | 2024 | Delays seen in seconds, not hours |
| Migration of 140 models to dbt | 2022 | Nightly build from 3 hours to 25 minutes |
`;

/** What the app understood from Camille's Skills part, ready to apply. */
export const SKILLS_READ = [
  "What I have, streaming: adds Flink",
  "Close to what I have: adds machine_learning (MLflow, feature stores)",
  "Years of experience: 6 becomes 7",
];

/** Job title ideas the assistant gave Camille. */
export const TITLE_IDEAS: TitleSuggestions = {
  status: "idle",
  needs: null,
  suggestions: [
    {
      title: "Analytics engineer",
      reason:
        "Your dbt migration and the tables you document are the core of this role.",
      language: "en",
    },
    {
      title: "Streaming data engineer",
      reason: "Kafka and Flink in production at Lumen Freight.",
      language: "en",
    },
    {
      title: "Ingénieure data plateforme",
      reason: "The same work, as French postings in Lyon name it.",
      language: "fr",
    },
  ],
  refreshed_at: "2026-11-05T08:00:00Z",
  error: null,
  note: null,
  messages: [],
  message_max: 300,
  estimate: {
    tokens: { low: 20000, high: 39000 },
    cost: { currency: "USD", low: "0.08", high: "0.16" },
  },
  cost_note: null,
};
