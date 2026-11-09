import { Plus } from "lucide-react";

import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import type { BaseCv } from "@/features/documents/api/base-cvs-api";
import { tooManyCvs } from "@/features/documents/helpers/base-cvs";
import { CaptureStatus } from "../capture-status";
import { CvActions } from "./cv-actions";
import { CvRow } from "./cv-row";
import { NoCvYet } from "./no-cv-yet";

/** Your CVs on the profile page: the person's CVs (or the first build), "New CV", and when a posting's documents are written. */
export function CvOverview({
  cvs,
  max,
  building,
  noExperience,
  onNew,
  onBuild,
}: {
  cvs: BaseCv[];
  max: number;
  building: boolean;
  noExperience: boolean;
  onNew: () => void;
  onBuild: () => void;
}) {
  const full = cvs.length >= max;
  return (
    <Section
      title="Your CVs"
      description="The default CV is the one every other document is written from."
      actions={
        cvs.length > 0 ? (
          <Button disabled={full || building} onClick={onNew}>
            <Plus aria-hidden="true" />
            New CV
          </Button>
        ) : null
      }
    >
      {full && cvs.length > 0 ? (
        <p className="text-sm text-muted-foreground">{tooManyCvs(max)}</p>
      ) : null}
      {cvs.length === 0 ? (
        <NoCvYet
          building={building}
          noExperience={noExperience}
          onBuild={onBuild}
        />
      ) : (
        <Card className="py-0">
          <ul className="flex flex-col divide-y" aria-label="Your CVs">
            {cvs.map((one) => (
              <li
                key={one.id}
                className="flex flex-col gap-3 px-4 py-4 sm:px-6 sm:py-5"
              >
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <CvRow cv={one} only={cvs.length === 1} />
                  <CvActions cv={one} cvs={cvs} />
                </div>
                {one.source === "uploaded" ? (
                  <CaptureStatus key={one.id} cv={one} />
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <p
        className="text-sm text-muted-foreground"
        data-testid="written-on-click"
      >
        A posting's CV and letter are written when you click Write on the job
        page, or Apply.
      </p>
    </Section>
  );
}
