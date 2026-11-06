/** The automatic search schedule as GET /api/jobs/sweeps reports it, and its one line summary. */
import { describeSchedule } from "@/features/profile/helpers/schedule";

import {
  formatInZone,
  weekdays,
  type SweepSchedule,
} from "../helpers/schedule";
import type { ScheduleState } from "./sweeps-api";
import { useSweeps } from "./use-sweeps";

/** The schedule as the list of sweeps reports it, and the line that sums it up. */
export function useSchedule() {
  const query = useSweeps();
  return query.data?.schedule;
}

/** The schedule part of the list answer, in the profile's shape. */
export function scheduleOf(state: ScheduleState): SweepSchedule {
  return {
    enabled: state.enabled,
    days: weekdays(state.days),
    times: [...state.times],
    timezone: state.timezone,
  };
}

/** The same wording as the Search tab of the profile ("Automatic search: on, every day at
 * 06:17 (Europe/Paris)"), with the next run. */
export function scheduleLine(state: ScheduleState | undefined): string {
  if (!state) return "Loading the schedule";
  const line = `Automatic search: ${describeSchedule(scheduleOf(state))}.`;
  if (!state.enabled) return line;
  if (!state.active)
    return `${line} No job website is ticked in Find jobs, so nothing runs.`;
  return state.next_run
    ? `${line} Next: ${formatInZone(new Date(state.next_run), state.timezone)}.`
    : line;
}
