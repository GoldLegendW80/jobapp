/** The automatic search schedule (search.sweep_schedule) in words, for the Search tab. */
import { WEEKDAYS, type SweepSchedule } from "./api";

const DAY_NAMES: Record<string, string> = {
  monday: "Monday",
  tuesday: "Tuesday",
  wednesday: "Wednesday",
  thursday: "Thursday",
  friday: "Friday",
  saturday: "Saturday",
  sunday: "Sunday",
};

const list = (items: string[]) =>
  items.length <= 1
    ? (items[0] ?? "")
    : `${items.slice(0, -1).join(", ")} and ${items.at(-1) ?? ""}`;

/** "off", or "on, every day at 06:17 (Europe/Paris)", "on, Monday and Friday at 06:17 and 18:30 (...)". */
export function describeSchedule(schedule: SweepSchedule): string {
  if (!schedule.enabled) return "off";
  const days = WEEKDAYS.filter((day) => schedule.days.includes(day));
  const when =
    days.length === 7
      ? "every day"
      : days.length === 5 &&
          !days.includes("saturday") &&
          !days.includes("sunday")
        ? "weekdays"
        : list(days.map((day) => DAY_NAMES[day] ?? day));
  const times = [...schedule.times].sort();
  return `on, ${when} at ${list(times)} (${schedule.timezone})`;
}
