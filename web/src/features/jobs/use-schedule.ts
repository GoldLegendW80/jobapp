/** The automatic search schedule as GET /api/jobs/sweeps reports it, and its one line summary. */
import { useQuery } from "@tanstack/react-query";

import { describeSchedule } from "@/features/profile/schedule";

import { formatInZone, weekdays, type SweepSchedule } from "./schedule";
import { listSweeps, sweepsKey, type ScheduleState } from "./sweeps-api";

/** The schedule as the list of sweeps reports it, and the line that sums it up. */
export function useSchedule() {
  const query = useQuery({
    queryKey: sweepsKey,
    queryFn: ({ signal }) => listSweeps(signal),
  });
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
    return `${line} No board is enabled in your profile, so nothing runs.`;
  return state.next_run
    ? `${line} Next: ${formatInZone(new Date(state.next_run), state.timezone)}.`
    : line;
}
