import { LoadError } from "@/components/blocks/load-error";
import { Section } from "@/components/blocks/section";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfile } from "@/features/profile/api/use-profile";

import { useBaseCvs } from "@/features/documents/api/use-base-cvs";
import { useNewBaseCv } from "@/features/documents/api/use-new-base-cv";
import { CV_LIST_PATH } from "@/features/documents/helpers/base-cvs";
import { CvOverview } from "./cvs/cv-overview";
import { NewCvDialog } from "./cvs/new-cv-dialog";
import { UploadPanel } from "@/components/shared/documents/upload-panel";

/**
 * The CVs of the profile page, two sections: Your CVs (the person's base CVs, one of them the
 * default; each opens on its own page, /profile/cv/:id) and Upload your CV.
 */
export function YourCvs() {
  const list = useBaseCvs();
  const build = useNewBaseCv();
  const { create } = build;
  // The same read as the profile page's own, so it is already in the cache.
  const profile = useProfile();

  if (list.isPending) {
    // No heading yet: "Your CVs" shows with the list, so whatever waits for it finds the list.
    return (
      <div role="status" aria-live="polite" className="flex flex-col gap-4">
        <span className="sr-only">Loading your CVs</span>
        <Skeleton aria-hidden="true" className="h-6 w-32" />
        <Skeleton aria-hidden="true" className="h-36" />
      </div>
    );
  }
  if (list.isError) {
    return (
      <Section title="Your CVs">
        <LoadError query={list}>Your CVs could not be loaded.</LoadError>
      </Section>
    );
  }

  const { base_cvs: cvs, max } = list.data;

  return (
    <div className="flex flex-col gap-8">
      <CvOverview
        cvs={cvs}
        max={max}
        building={create.isPending}
        noExperience={
          profile.data !== undefined &&
          profile.data.sections.experience.employers.length === 0
        }
        onNew={() => {
          build.setCreating(true);
        }}
        onBuild={() => {
          build.start({ from: "profile" });
        }}
      />
      <Section
        title="Upload your CV"
        description="The app keeps the file and turns it into a CV you can edit."
      >
        <UploadPanel
          titled={false}
          openHref={(to) => `${CV_LIST_PATH}/${to}`}
        />
      </Section>
      <NewCvDialog
        key={build.creating ? "open" : "closed"}
        open={build.creating}
        cvs={cvs}
        busy={create.isPending}
        onClose={() => {
          build.setCreating(false);
        }}
        onCreate={build.start}
      />
    </div>
  );
}
