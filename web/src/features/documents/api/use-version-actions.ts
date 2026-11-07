import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  approveVersion,
  documentKey,
  failureSentence,
  restoreVersion,
  versionsKey,
  type DocumentOut,
  type VersionSummary,
} from "./documents-api";
import { postingDocumentsKey } from "./posting-documents-api";

/**
 * Restore and Approve of a version. A restore asks first (`restoring` is the version the dialog
 * names), then puts its text into the draft; an approve marks the version for the posting.
 */
export function useVersionActions(document: DocumentOut) {
  const queryClient = useQueryClient();
  const [restoring, setRestoring] = useState<VersionSummary>();
  const restore = useMutation({
    mutationFn: (number: number) => restoreVersion(document.id, number),
    onSuccess: (answer, number) => {
      queryClient.setQueryData(documentKey(document.id), answer);
      void queryClient.invalidateQueries({
        queryKey: versionsKey(document.id),
      });
      setRestoring(undefined);
      toast.success(
        `The draft has the text of version ${String(number)}. Its page is being drawn.`,
      );
    },
    onError: (error) => {
      toast.error(failureSentence(error));
    },
  });
  const approve = useMutation({
    mutationFn: (number: number) => approveVersion(document.id, number),
    onSuccess: (_, number) => {
      void queryClient.invalidateQueries({
        queryKey: versionsKey(document.id),
      });
      if (document.posting_id !== null)
        void queryClient.invalidateQueries({
          queryKey: postingDocumentsKey(document.posting_id),
        });
      toast.success(`Version ${String(number)} is approved.`);
    },
    onError: (error) => {
      toast.error(failureSentence(error));
    },
  });
  return {
    restoring,
    askRestore: setRestoring,
    restore,
    approve,
    busy: restore.isPending || approve.isPending,
  };
}
