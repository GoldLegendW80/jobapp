import { ArrowLeft } from "lucide-react";
import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import type { BaseCv } from "@/features/documents/api/base-cvs-api";
import { CV_LIST_PATH } from "@/features/documents/helpers/base-cvs";
import { SOURCE_LABELS } from "@/features/documents/helpers/labels";
import { CaptureStatus } from "../capture-status";
import { DocumentView } from "@/components/shared/documents/document-view";
import { CvActions } from "./cv-actions";

function BackToCvs({ variant }: { variant: "ghost" | "outline" }) {
  return (
    <Button
      asChild
      variant={variant}
      size={variant === "ghost" ? "sm" : undefined}
    >
      <Link to={CV_LIST_PATH}>
        <ArrowLeft aria-hidden="true" />
        All my CVs
      </Link>
    </Button>
  );
}

/** One CV opened (/profile/cv/:id): its name, its actions and its document view. */
export function OpenedCv({ cv, cvs }: { cv: BaseCv; cvs: BaseCv[] }) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <BackToCvs variant="ghost" />
      </div>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-words text-lg font-semibold tracking-tight">
            {cv.name}
          </h2>
          {cv.is_default ? <Badge>Default</Badge> : null}
        </div>
        <p className="text-sm text-muted-foreground">
          {SOURCE_LABELS[cv.source]}
        </p>
        <CvActions cv={cv} cvs={cvs} backToList />
      </div>
      {cv.source === "uploaded" ? <CaptureStatus key={cv.id} cv={cv} /> : null}
      <DocumentView
        documentId={cv.id}
        generation={cv.generation}
        capture={cv.design_generation}
      />
    </div>
  );
}

/** The address of a CV that is not in the list, deleted in another tab perhaps. */
export function CvNotFound() {
  return (
    <div className="flex flex-col items-start gap-3">
      <Alert>
        <AlertTitle>This CV does not exist</AlertTitle>
        <AlertDescription>
          It may have been deleted in another tab.
        </AlertDescription>
      </Alert>
      <BackToCvs variant="outline" />
    </div>
  );
}
