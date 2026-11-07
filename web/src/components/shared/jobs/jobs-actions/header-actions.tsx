import type { FindJobs } from "@/features/jobs/hooks/use-find-jobs";
import {
  FindJobsButton,
  FindJobsSetup,
  SweepSummaryDialog,
} from "../find-jobs";
import { AddPostingButton } from "./add-posting-button";
import { PageMenu } from "./page-menu";

/**
 * The Jobs header's actions, the same on List and Map: Find jobs (the primary), Add a posting,
 * and the More actions menu; with the Find jobs setup and the summary of a search that ends.
 * With no posting at all, the empty view holds Find jobs and Add a posting instead.
 */
export function HeaderActions({
  finder,
  selected,
  empty,
}: {
  finder: FindJobs;
  /** The ticked postings, for "Rescore the selected postings". */
  selected: string[];
  empty: boolean;
}) {
  return (
    <>
      {empty ? null : <FindJobsButton finder={finder} />}
      {empty ? null : <AddPostingButton />}
      <PageMenu selected={selected} runner={finder.runner} />
      <FindJobsSetup finder={finder} />
      <SweepSummaryDialog runner={finder.runner} />
    </>
  );
}
