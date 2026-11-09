import { FileText } from "lucide-react";

import { EmptyState } from "@/components/blocks/empty-state";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

/** Your CVs before the first one: what a CV is for, and the build from the profile. */
export function NoCvYet({
  building,
  noExperience,
  onBuild,
}: {
  building: boolean;
  noExperience: boolean;
  onBuild: () => void;
}) {
  return (
    <section aria-labelledby="empty-heading">
      <EmptyState
        icon={FileText}
        title={<h3 id="empty-heading">You have no CV yet</h3>}
        description="The app can write one from your profile. It becomes the default and every document for a posting starts from it."
        actions={
          <div className="flex flex-col items-center gap-3">
            {noExperience ? (
              <p
                className="text-sm font-medium"
                data-testid="no-experience-line"
              >
                Add your CV or fill your experience to get documents for each
                posting.
              </p>
            ) : null}
            <Button disabled={building} onClick={onBuild}>
              {building ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Build it from my profile
            </Button>
          </div>
        }
      />
    </section>
  );
}
