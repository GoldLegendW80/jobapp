import { CountBadge } from "@/components/blocks/count-badge";

import { useActivityCount } from "../api/use-activity";

/**
 * How many of the person's workflow runs are under way, beside the app shell's Activity link, in
 * the look of the Applications count. Nothing when none is or no count was read yet; a failed
 * read keeps the last count.
 */
export function ActivityCount() {
  const query = useActivityCount();
  return <CountBadge count={query.data ?? 0} words="under way" />;
}
