import { Plus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

import { AddPostingDialog } from "./add-posting-dialog";

/** "Add a posting" in the page's header, or "Add a posting by hand" beside Find jobs in the empty
 * list: outline either way (Find jobs is the one primary action), both open the same dialog. */
export function AddPostingButton({ byHand = false }: { byHand?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="outline"
        onClick={() => {
          setOpen(true);
        }}
      >
        <Plus aria-hidden="true" />
        {byHand ? "Add a posting by hand" : "Add a posting"}
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
