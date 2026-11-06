import { SourcesEditor } from "@/components/shared/jobs/sources/sources-editor";
import type { Sources } from "@/features/jobs/api/use-sources";
import type { Search } from "@/features/profile/api/profile-api";

/** Step 4, Websites to search: the list of Find jobs, under the step's own title. */
export function StepSites({
  sources,
  reviewed,
}: {
  sources: Sources;
  reviewed: Search;
}) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Tell the app where to look for postings: add a few websites now. You can
        change the list any time from Jobs, Find jobs.
      </p>
      <SourcesEditor sources={sources} reviewed={reviewed} showTitle={false} />
    </div>
  );
}
