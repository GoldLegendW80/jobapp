import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

import { type BaseCv } from "@/features/documents/api/base-cvs-api";
import { failureSentence } from "@/features/documents/api/documents-api";
import { useUpdateBaseCv } from "@/features/documents/api/use-base-cvs";
import { DeleteCvDialog } from "./delete-cv-dialog";
import { RenameCvDialog } from "./rename-cv-dialog";

/** Make default, Rename and Delete of one CV; `backToList` returns to the list after a delete. */
export function CvActions({
  cv,
  cvs,
  backToList = false,
}: {
  cv: BaseCv;
  cvs: BaseCv[];
  backToList?: boolean;
}) {
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const makeDefault = useUpdateBaseCv(cv.id, {
    onError: (error) => {
      toast.error(failureSentence(error));
    },
  });

  return (
    <div className="flex flex-wrap gap-2">
      {cv.is_default ? null : (
        <Button
          variant="outline"
          size="sm"
          disabled={makeDefault.isPending}
          aria-label={`Make default for ${cv.name}`}
          onClick={() => {
            makeDefault.mutate({ default: true });
          }}
        >
          Make default
        </Button>
      )}
      <Button
        variant="outline"
        size="sm"
        aria-label={`Rename ${cv.name}`}
        onClick={() => {
          setRenaming(true);
        }}
      >
        Rename
      </Button>
      <Button
        variant="outline"
        size="sm"
        aria-label={`Delete ${cv.name}`}
        onClick={() => {
          setDeleting(true);
        }}
      >
        Delete
      </Button>

      <RenameCvDialog
        key={renaming ? "open" : "closed"}
        cv={cv}
        open={renaming}
        onClose={() => {
          setRenaming(false);
        }}
      />
      <DeleteCvDialog
        cv={cv}
        others={cvs.length - 1}
        open={deleting}
        backToList={backToList}
        onClose={() => {
          setDeleting(false);
        }}
      />
    </div>
  );
}
