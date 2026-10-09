/**
 * The automatic search schedule (`search.sweep_schedule` of the profile) and when it fires next,
 * computed exactly as api/app/jobs/schedule.py does, daylight saving time included:
 * - a local time skipped by the spring transition fires when the clock jumps over it;
 * - a local time repeated by the autumn transition fires at its first occurrence.
 */
import {
  EMPTY,
  WEEKDAYS,
  type SweepSchedule,
  type Weekday,
} from "@/features/profile/api";

// One shape for the whole app: the profile's (features/profile/api.ts, schemas.py SweepSchedule).
export { WEEKDAYS, type SweepSchedule, type Weekday };

export const WEEKDAY_LABELS: Record<Weekday, string> = {
  monday: "Mon",
  tuesday: "Tue",
  wednesday: "Wed",
  thursday: "Thu",
  friday: "Fri",
  saturday: "Sat",
  sunday: "Sun",
};

export const TIMES_MAX = 4;
/** Off, every day at 06:17 Europe/Paris: the empty profile's value. */
export const DEFAULT_SCHEDULE: SweepSchedule = EMPTY.search.sweep_schedule;

/** The weekday names of a list read from the api, unknown ones dropped, in week order. */
export function weekdays(days: readonly string[]): Weekday[] {
  return WEEKDAYS.filter((day) => days.includes(day));
}

const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;
const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(timeZone: string): Intl.DateTimeFormat {
  let found = formatters.get(timeZone);
  if (!found) {
    found = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      weekday: "long",
    });
    formatters.set(timeZone, found);
  }
  return found;
}

interface Wall {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  weekday: Weekday;
}

/** The wall clock of `zone` at the instant `ms`. */
export function wallClock(ms: number, zone: string): Wall {
  const parts: Record<string, string> = {};
  for (const part of formatter(zone).formatToParts(new Date(ms)))
    parts[part.type] = part.value;
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour) % 24,
    minute: Number(parts.minute),
    weekday: (parts.weekday ?? "").toLowerCase() as Weekday,
  };
}

function wallMs(wall: Omit<Wall, "weekday">): number {
  return Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute);
}

/** The zone's offset from UTC at the instant `ms`, in milliseconds. */
function offset(ms: number, zone: string): number {
  const at = Math.floor(ms / MINUTE) * MINUTE;
  return wallMs(wallClock(at, zone)) - at;
}

/** The instant of the local wall time (DST rules of the module comment). */
export function zonedInstant(
  wall: Omit<Wall, "weekday">,
  zone: string,
): number {
  const guess = wallMs(wall);
  const candidates = [guess - DAY, guess, guess + DAY].map(
    (at) => guess - offset(at, zone),
  );
  const exact = candidates
    .filter((at) => wallMs(wallClock(at, zone)) === guess)
    .sort((a, b) => a - b);
  const first = exact[0];
  if (first !== undefined) return first;
  // In the spring gap: the first minute whose local clock reads the wall time or later.
  let at = Math.min(...candidates);
  const end = Math.max(...candidates);
  while (at < end && wallMs(wallClock(at, zone)) < guess) at += MINUTE;
  return at;
}

/** When the schedule fires next after `now`, or null when it is off or has no slot. */
export function nextRun(schedule: SweepSchedule, now: Date): Date | null {
  if (!schedule.enabled || !schedule.days.length || !schedule.times.length)
    return null;
  const today = wallClock(now.getTime(), schedule.timezone);
  const start = Date.UTC(today.year, today.month - 1, today.day);
  let best: number | null = null;
  for (let d = -1; d <= 8; d += 1) {
    const day = new Date(start + d * DAY);
    const weekday = WEEKDAYS[(day.getUTCDay() + 6) % 7];
    if (weekday === undefined || !schedule.days.includes(weekday)) continue;
    for (const time of schedule.times) {
      const [hour, minute] = time.split(":").map(Number);
      const at = zonedInstant(
        {
          year: day.getUTCFullYear(),
          month: day.getUTCMonth() + 1,
          day: day.getUTCDate(),
          hour: hour ?? 0,
          minute: minute ?? 0,
        },
        schedule.timezone,
      );
      if (at > now.getTime() && (best === null || at < best)) best = at;
    }
  }
  return best === null ? null : new Date(best);
}

/** "Sat 10 Oct, 06:17 (Europe/Paris)": an instant shown in the schedule's own zone. */
export function formatInZone(date: Date, zone: string): string {
  const text = new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
  return `${text} (${zone})`;
}

/** Every IANA zone the browser knows, the given ones first if missing. */
export function timeZones(...extra: string[]): string[] {
  const supported =
    typeof Intl.supportedValuesOf === "function"
      ? Intl.supportedValuesOf("timeZone")
      : [];
  const all = new Set([
    ...supported,
    DEFAULT_SCHEDULE.timezone,
    "UTC",
    ...extra,
  ]);
  return [...all].sort();
}
