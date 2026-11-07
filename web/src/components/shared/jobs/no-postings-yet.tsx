import { Briefcase } from "lucide-react";

import { EmptyState } from "@/components/blocks/empty-state";

import type { FindJobs } from "@/features/jobs/hooks/use-find-jobs";
import { FindJobsButton } from "./find-jobs";
import { AddPostingButton } from "./jobs-actions/add-posting-button";

/** No posting at all and no filter, on List or Map: how to get some. */
export function NoPostingsYet({ finder }: { finder: FindJobs }) {
  return (
    <section aria-labelledby="jobs-empty-title">
      <EmptyState
        icon={Briefcase}
        title={<h2 id="jobs-empty-title">No postings yet</h2>}
        description="Tell the app where to look and it brings postings here."
        actions={
          <>
            <FindJobsButton finder={finder} />
            <AddPostingButton byHand />
          </>
        }
      />
    </section>
  );
}
