import { Link } from "react-router";

import { ADZUNA_KEY_PLACE, adzunaOf } from "@/features/jobs/helpers/job-feeds";
import { useJobFeeds } from "@/features/jobs/api/use-job-feeds";

/** Under Adzuna's line while its key is missing: without one it is skipped. */
export function AdzunaNote() {
  const feeds = useJobFeeds();
  const adzuna = adzunaOf(feeds.data);
  if (feeds.isPending || adzuna?.key_stored) return null;
  return (
    <p className="text-xs">
      Adzuna needs your own free key, or it is skipped.{" "}
      <Link className="underline underline-offset-2" to={ADZUNA_KEY_PLACE}>
        Add it in Account
      </Link>
      .
    </p>
  );
}
