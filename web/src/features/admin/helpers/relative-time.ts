const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

const format = new Intl.RelativeTimeFormat("en", { numeric: "always" });
// A value this close to a whole unit reads as that unit: a link made a second ago for 7 days
// says "in 7 days", not "in 6 days".
const NEARLY = 0.98;

/**
 * "in 7 days", "in 40 minutes", "3 hours ago": the largest unit the time span (nearly) reaches,
 * rounded to the nearest whole amount. Under a minute reads "now".
 */
export function relativeTime(target: string | Date, now: Date = new Date()) {
  const when = typeof target === "string" ? new Date(target) : target;
  const diff = when.getTime() - now.getTime();
  if (Number.isNaN(diff)) return "";
  for (const [unit, ms] of UNITS) {
    if (Math.abs(diff) >= ms * NEARLY)
      return format.format(Math.round(diff / ms), unit);
  }
  return "now";
}

/** The exact time, for a tooltip or a datetime attribute. */
export function exactTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
      });
}
