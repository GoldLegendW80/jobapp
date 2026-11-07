/** The fields of "Edit details" as typed, and the change they make to the posting. Pure. */
import type { JobDetail, JobPatch } from "../api/jobs-api";
import { typedPay } from "./pay-words";

export interface Details {
  company: string;
  title: string;
  location: string;
  contract_type: string;
  url: string;
  work_mode: string;
  pay_min: string;
  pay_max: string;
  pay_currency: string;
  pay_period: string;
}

const PAY_KEYS = ["pay_min", "pay_max", "pay_currency", "pay_period"] as const;
const DETAIL_KEYS = [
  "company",
  "title",
  "location",
  "contract_type",
  "url",
  "work_mode",
] as const;

export function detailsOf(job: JobDetail): Details {
  return {
    company: job.company,
    title: job.title,
    location: job.location,
    contract_type: job.contract_type,
    url: job.url ?? "",
    work_mode: job.work_mode ?? "",
    pay_min: job.pay_min == null ? "" : String(job.pay_min),
    pay_max: job.pay_max == null ? "" : String(job.pay_max),
    pay_currency: job.pay_currency ?? "EUR",
    pay_period: job.pay_period ?? "year",
  };
}

/** What saving sends: only the fields changed against `initial`, the pay as one range when any
 * of its parts changed; "pay-invalid" when the pay typed is no pay. */
export function detailsPatch(
  values: Details,
  initial: Details,
): JobPatch | "pay-invalid" {
  const patch: JobPatch = {};
  for (const name of DETAIL_KEYS) {
    if (values[name].trim() === initial[name].trim()) continue;
    if (name === "url") patch.url = values.url.trim() || null;
    else if (name === "work_mode") patch.work_mode = values.work_mode || null;
    else patch[name] = values[name].trim();
  }
  const payChanged = PAY_KEYS.some(
    (name) => values[name].trim() !== initial[name].trim(),
  );
  if (payChanged) {
    const pay = typedPay(
      values.pay_min,
      values.pay_max,
      values.pay_currency,
      values.pay_period,
    );
    if (pay === undefined) return "pay-invalid";
    const hadPay = initial.pay_min !== "" || initial.pay_max !== "";
    if (pay !== null || hadPay) patch.pay = pay;
  }
  return patch;
}
