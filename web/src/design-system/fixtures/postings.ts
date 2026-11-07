import type { JobDetail, JobListItem } from "@/features/jobs/api/jobs-api";
import { makeDetail, makeJob } from "@/test/jobs";

/** Invented postings for the page stories: believable, never a real company or person. */
const ROWS: Array<Partial<JobListItem>> = [
  {
    title: "Senior data engineer",
    company: "Lumen Freight",
    location: "Lyon, France",
    work_mode: "hybrid",
    contract_type: "CDI",
    status: "cv_ready",
    fit: 91,
    pay_min: 55000,
    pay_max: 65000,
    pay_currency: "EUR",
    pay_period: "year",
    competencies: ["Python", "dbt", "Kafka", "Airflow"],
    gaps: ["Scala"],
  },
  {
    title: "Analytics engineer",
    company: "Kestrel Health",
    location: "Remote, France",
    work_mode: "remote",
    contract_type: "CDI",
    status: "shortlisted",
    fit: 84,
    pay_min: 52000,
    pay_max: 58000,
    pay_currency: "EUR",
    pay_period: "year",
    competencies: ["SQL", "dbt", "Looker"],
    gaps: ["Healthcare data standards"],
  },
  {
    title: "Data platform engineer, streaming and real time pricing",
    company: "Atelier Brume",
    location: "Paris, France",
    work_mode: "onsite",
    contract_type: "Freelance",
    status: "applied",
    fit: 77,
    pay_min: 600,
    pay_max: 650,
    pay_currency: "EUR",
    pay_period: "day",
    competencies: ["Kafka", "Flink", "Kubernetes"],
    gaps: ["Rust"],
    applied_on: "2026-11-02",
  },
  {
    title: "Machine learning engineer",
    company: "Orpheo Maps",
    location: "Grenoble, France",
    work_mode: "hybrid",
    contract_type: "CDI",
    status: "interview",
    fit: 72,
    competencies: ["PyTorch", "Python", "MLOps"],
    gaps: ["Computer vision"],
    applied_on: "2026-10-27",
  },
  {
    title: "Product analyst",
    company: "Maison Tilleul",
    location: "Bordeaux, France",
    work_mode: "hybrid",
    contract_type: "CDD",
    status: "staged",
    fit: 58,
    pay_min: 42000,
    pay_max: 46000,
    pay_currency: "EUR",
    pay_period: "year",
    competencies: ["SQL", "Amplitude"],
    gaps: ["A/B testing at scale", "Retail"],
  },
  {
    title: "BI developer",
    company: "Verre et Cie",
    location: "Nantes, France",
    contract_type: "CDI",
    status: "found",
    fit: 44,
    competencies: ["Power BI"],
    gaps: ["SSIS", "Azure"],
  },
  {
    title: "Data engineer intern",
    company: "Pollen Robotics Lab",
    location: "Bordeaux, France",
    contract_type: "Stage",
    status: "found",
    fit: null,
    pay_min: 1400,
    pay_max: 1400,
    pay_currency: "EUR",
    pay_period: "month",
  },
  {
    title: "Database administrator",
    company: "Ferrovia Nord",
    location: "Lille, France",
    contract_type: "CDI",
    status: "rejected",
    fit: 23,
    competencies: ["PostgreSQL"],
    gaps: ["Oracle", "On call"],
  },
];

const DESCRIPTION = `Lumen Freight moves 40,000 pallets a day across Europe. The pricing team needs someone to own its pipelines, from the raw tracking events to the tables our planners read every morning.

What you will do:
- Build and run streaming jobs on Kafka and batch models in dbt.
- Work with the planners who use your tables, every week.
- Keep the costs of the platform in check.

What we look for: four years or more with Python and SQL, a pipeline you shipped and still run, and curiosity about logistics.`;

export const POSTINGS: JobListItem[] = ROWS.map((row, index) =>
  makeJob(index + 1, {
    url: `https://careers.example.org/${index + 1}`,
    description: DESCRIPTION,
    found_on: `2026-11-0${(index % 5) + 1}`,
    published_on: `2026-10-${20 + index}`,
    ...row,
  }),
);

/** The first posting with everything its own page shows. */
export const POSTING_DETAIL: JobDetail = makeDetail({
  ...POSTINGS[0],
  description: DESCRIPTION,
});
