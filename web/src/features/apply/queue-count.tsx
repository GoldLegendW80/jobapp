import { useQuery } from "@tanstack/react-query";

import { getQueueCount, queueCountKey } from "./queue-api";
import { COUNT_POLL_MS } from "./queue-state";

/**
 * How many applications wait for the person, beside the app shell's Applications link: a badge,
 * and the same number in words for a screen reader. Nothing when none waits or the count failed.
 */
export function QueueCount() {
  const query = useQuery({
    queryKey: queueCountKey,
    queryFn: ({ signal }) => getQueueCount(signal),
    refetchInterval: COUNT_POLL_MS,
  });
  const count = query.data ?? 0;
  if (count <= 0) return null;
  return (
    <>
      <span
        aria-hidden="true"
        className="ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground tabular-nums"
      >
        {count > 99 ? "99+" : count}
      </span>
      <span className="sr-only">, {count} waiting for you</span>
    </>
  );
}
