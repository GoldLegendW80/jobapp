import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";

import type { BaseCv } from "@/features/documents/api/base-cvs-api";
import {
  CV_LIST_PATH,
  cvStateLine,
} from "@/features/documents/helpers/base-cvs";
import {
  formatWhen,
  isOpen,
  SOURCE_LABELS,
  templateLabel,
} from "@/features/documents/helpers/labels";

/** One CV of the list: its name (the link that opens it), where it comes from, what it is doing. */
export function CvRow({ cv, only }: { cv: BaseCv; only: boolean }) {
  const line = cvStateLine(cv, only);
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          to={`${CV_LIST_PATH}/${cv.id}`}
          className="min-w-0 break-words text-base font-semibold underline-offset-4 hover:underline"
        >
          {cv.name}
        </Link>
        {cv.is_default ? <Badge>Default</Badge> : null}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{SOURCE_LABELS[cv.source]}</span>
        <span>Template: {templateLabel(cv.template_slug)}</span>
        <span>
          {cv.last_version_at === null
            ? "No version yet"
            : `Last version ${formatWhen(cv.last_version_at)}`}
        </span>
      </div>
      {line !== null ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          {isOpen(cv.generation) && cv.generation?.status !== "waiting" ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : null}
          {line}
        </p>
      ) : null}
    </div>
  );
}
