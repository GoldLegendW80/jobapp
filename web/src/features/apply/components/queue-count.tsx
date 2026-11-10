import { CountBadge } from "@/components/blocks/count-badge";

import { useQueueCount } from "../api/use-queue";

/**
 * How many applications wait for the person, beside the app shell's Applications link: a badge,
 * and the same number in words for a screen reader. Nothing when none waits or the count failed.
 */
export function QueueCount() {
  const query = useQueueCount();
  return <CountBadge count={query.data ?? 0} words="waiting for you" />;
}
