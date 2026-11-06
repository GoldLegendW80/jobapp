import { Pencil } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import type { Save } from "@/features/jobs/api/use-job-editor";
import { EditDetailsDialog } from "./edit-details-dialog";

/** "Edit details" beside the posting's title: opens the details dialog. */
export function EditDetailsButton({
  job,
  save,
}: {
  job: JobDetail;
  save: Save;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      >
        <Pencil aria-hidden="true" />
        Edit details
      </Button>
      {open ? (
        <EditDetailsDialog
          job={job}
          save={save}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}
