import { MoreHorizontal, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { type BaseCv } from "@/features/documents/api/base-cvs-api";
import { failureSentence } from "@/features/documents/api/documents-api";
import { useUpdateBaseCv } from "@/features/documents/api/use-base-cvs";
import { DeleteCvDialog } from "./delete-cv-dialog";
import { RenameCvDialog } from "./rename-cv-dialog";

/**
 * Make default, Rename and Delete of one CV: small buttons on a row of the list, or the actions
 * of the CV's own page (`page`), where Delete is under More actions and leads back to the list.
 */
export function CvActions({
  cv,
  cvs,
  page = false,
}: {
  cv: BaseCv;
  cvs: BaseCv[];
  page?: boolean;
}) {
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const makeDefault = useUpdateBaseCv(cv.id, {
    onError: (error) => {
      toast.error(failureSentence(error));
    },
  });
  const size = page ? "default" : "sm";

  return (
    <div className="flex flex-wrap gap-2 md:shrink-0">
      {cv.is_default ? null : (
        <Button
          variant="outline"
          size={size}
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
        size={size}
        aria-label={`Rename ${cv.name}`}
        onClick={() => {
          setRenaming(true);
        }}
      >
        Rename
      </Button>
      {page ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="icon" aria-label="More actions">
              <MoreHorizontal aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => {
                setDeleting(true);
              }}
            >
              <Trash2 aria-hidden="true" />
              Delete this CV
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
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
      )}

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
        backToList={page}
        onClose={() => {
          setDeleting(false);
        }}
      />
    </div>
  );
}
