import { Link } from "react-router";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { UploadPanel } from "@/components/shared/documents/upload-panel";
import { RunProgress } from "@/components/shared/ingestion/run-progress";
import { RunStart } from "@/components/shared/ingestion/run-start";
import { StepDocuments } from "@/components/shared/ingestion/step-documents";
import { WORDING } from "@/lib/wording";

/** Step 1, Your profile: the CV upload, and the reading of other documents under Advanced. */
export function StepProfile() {
  // The run part asks the api only once the person opens the disclosure.
  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        Upload your CV: the app keeps it as your base CV, and can fill your
        profile from it. You review every proposal before it is written. The
        reading goes on while you answer the next steps.
      </p>
      {/* Already inside the wizard's card: no second frame around it. */}
      <UploadPanel
        className="p-0 sm:p-0"
        openHref={(id) => `/profile/cv/${id}`}
      />
      <p className="text-sm text-muted-foreground">
        {WORDING.importOtherProfile}
      </p>
      <AdvancedSection pageKey="welcome-profile">
        <div className="flex flex-col gap-6 pt-4">
          <StepDocuments flat />
          <section
            aria-labelledby="welcome-read"
            className="flex flex-col gap-4"
          >
            <div className="flex flex-col gap-1">
              <h3
                id="welcome-read"
                className="text-base font-semibold tracking-tight"
              >
                {WORDING.readProfile}
              </h3>
              <p className="text-sm text-muted-foreground">
                Choose the documents, see the estimate, and start. The reading
                goes on while you answer the next steps.
              </p>
            </div>
            <RunStart flat />
            <RunProgress flat />
          </section>
        </div>
      </AdvancedSection>
      <p className="text-sm">
        <Link
          className="underline underline-offset-4"
          to="/profile/fill?step=review"
        >
          {WORDING.reviewProposals}
        </Link>{" "}
        <span className="text-muted-foreground">
          (opens Fill my profile once the reading is done; come back to this
          page after)
        </span>
      </p>
    </div>
  );
}
