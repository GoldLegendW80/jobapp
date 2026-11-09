import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { createBaseCv, type BaseCvRequest } from "./base-cvs-api";
import { failureSentence } from "./documents-api";
import { useRefreshBaseCvs } from "./use-base-cvs";

/** Making a new base CV: the "New CV" dialog, and the build it starts on the person's click. */
export function useNewBaseCv() {
  const refresh = useRefreshBaseCvs();
  const [creating, setCreating] = useState(false);

  const create = useMutation({
    mutationFn: createBaseCv,
    onSuccess: () => refresh(),
    onError: (error) => {
      toast.error(failureSentence(error));
      void refresh();
    },
  });

  const start = (request: BaseCvRequest) => {
    create.mutate(request, {
      onSuccess: () => {
        setCreating(false);
      },
    });
  };

  return {
    /** The "New CV" dialog is open. */
    creating,
    setCreating,
    create,
    start,
  };
}
