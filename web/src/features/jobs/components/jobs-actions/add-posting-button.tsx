import { Plus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

import { AddPostingDialog } from "./add-posting-dialog";

/** "Add a posting" in the page's header (outline: Find jobs is the page's one primary action),
 * or, as a link, "Add a posting by hand" in the empty list. Both open the same dialog. */
export function AddPostingButton({ asLink = false }: { asLink?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant={asLink ? "link" : "outline"}
        className={asLink ? "h-auto px-0 underline" : undefined}
        onClick={() => {
          setOpen(true);
        }}
      >
        {asLink ? null : <Plus aria-hidden="true" />}
        {asLink ? "Add a posting by hand" : "Add a posting"}
      </Button>
      {open ? (
        <AddPostingDialog
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}
