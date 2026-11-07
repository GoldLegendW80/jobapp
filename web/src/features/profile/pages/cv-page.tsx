import { ArrowLeft, FileX } from "lucide-react";
import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { Page } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { DocumentView } from "@/components/shared/documents/document-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useBaseCvs } from "@/features/documents/api/use-base-cvs";
import { SOURCE_LABELS } from "@/features/documents/helpers/labels";
import { useTitle } from "@/hooks/use-title";

import { CaptureStatus } from "../components/capture-status";
import { CvActions } from "../components/cvs/cv-actions";
import { CVS_PATH } from "../helpers/anchors";

const TRAIL = [{ to: CVS_PATH, label: "Profile" }];

/**
 * One base CV on its own page (/profile/cv/:id), below the profile: its name, Make default,
 * Rename and Delete, then for an uploaded CV the copy of its design, then the CV itself.
 */
export function CvPage() {
  const { id } = useParams();
  const list = useBaseCvs();
  const cvs = list.data?.base_cvs ?? [];
  const cv = cvs.find((one) => one.id === id);
  useTitle(cv?.name ?? "CV");

  if (list.isPending)
    return (
      // No h1 yet: the CV's name is the title, so whatever waits for it finds the page.
      <Page>
        <div role="status" aria-live="polite" className="flex flex-col gap-3">
          <span className="sr-only">Loading the CV</span>
          <Skeleton aria-hidden="true" className="h-4 w-20" />
          <Skeleton aria-hidden="true" className="h-9 w-64" />
        </div>
        <Skeleton aria-hidden="true" className="h-96" />
      </Page>
    );
  if (list.isError)
    return (
      <Page>
        <PageHeader trail={TRAIL} title="CV" />
        <LoadError query={list}>Your CVs could not be loaded.</LoadError>
      </Page>
    );
  if (cv === undefined)
    return (
      <Page>
        <PageHeader trail={TRAIL} title="CV" />
        <EmptyState
          icon={FileX}
          title={<h2>This CV does not exist</h2>}
          description="It may have been deleted in another tab."
          actions={
            <Button asChild variant="outline">
              <Link to={CVS_PATH}>
                <ArrowLeft aria-hidden="true" />
                All my CVs
              </Link>
            </Button>
          }
        />
      </Page>
    );

  return (
    <Page>
      <PageHeader
        trail={TRAIL}
        title={cv.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            {cv.is_default ? <Badge>Default</Badge> : null}
            <span>{SOURCE_LABELS[cv.source]}</span>
          </span>
        }
        actions={<CvActions cv={cv} cvs={cvs} page />}
      />
      {cv.source === "uploaded" ? <CaptureStatus key={cv.id} cv={cv} /> : null}
      <DocumentView
        documentId={cv.id}
        generation={cv.generation}
        capture={cv.design_generation}
      />
    </Page>
  );
}
