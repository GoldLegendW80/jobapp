import { Plus } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import type { BaseCv } from "@/features/documents/api/base-cvs-api";
import { tooManyCvs } from "@/features/documents/helpers/base-cvs";
import { CaptureStatus } from "../capture-status";
import { CvActions } from "./cv-actions";
import { CvRow } from "./cv-row";
import { NoCvYet } from "./no-cv-yet";

/** The list of the CV tab: the person's CVs (or the first build), "New CV", the threshold. */
export function CvOverview({
  cvs,
  max,
  building,
  consentMissing,
  consentReady,
  consentError,
  noExperience,
  minFit,
  onNew,
  onBuild,
}: {
  cvs: BaseCv[];
  max: number;
  building: boolean;
  consentMissing: boolean;
  consentReady: boolean;
  consentError: boolean;
  noExperience: boolean;
  minFit: number | undefined;
  onNew: () => void;
  onBuild: () => void;
}) {
  const full = cvs.length >= max;
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-lg font-semibold tracking-tight">Your CVs</h2>
          <p className="text-sm text-muted-foreground">
            The default CV is the one every other document is written from.
          </p>
        </div>
        {cvs.length > 0 ? (
          <div className="flex flex-col items-end gap-1">
            <Button disabled={full || building} onClick={onNew}>
              <Plus aria-hidden="true" />
              New CV
            </Button>
            {full ? (
              <p className="max-w-64 text-right text-xs text-muted-foreground">
                {tooManyCvs(max)}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {cvs.length === 0 ? (
        <NoCvYet
          building={building}
          consentMissing={consentMissing}
          consentReady={consentReady}
          consentError={consentError}
          noExperience={noExperience}
          onBuild={onBuild}
        />
      ) : (
        <ul className="flex flex-col gap-3" aria-label="Your CVs">
          {cvs.map((one) => (
            <li key={one.id}>
              <Card className="gap-3 p-4 sm:p-5">
                <CvRow cv={one} only={cvs.length === 1} />
                {one.source === "uploaded" ? (
                  <CaptureStatus key={one.id} cv={one} />
                ) : null}
                <CvActions cv={one} cvs={cvs} />
              </Card>
            </li>
          ))}
        </ul>
      )}

      {minFit !== undefined ? (
        <p className="text-sm text-muted-foreground" data-testid="threshold">
          Documents are written for new postings with a fit of {minFit} or more.{" "}
          <Link
            to="/settings/search"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Change it in the Search options
          </Link>
          .
        </p>
      ) : null}
    </>
  );
}
