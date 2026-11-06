import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { createBaseCv, type BaseCvRequest } from "./base-cvs-api";
import { failureSentence } from "./documents-api";
import { acceptConsent, consentKey, getConsent } from "./spend-api";
import { useRefreshBaseCvs } from "./use-base-cvs";

/**
 * Making a new base CV: the "New CV" dialog, and the consent that a build from the profile asks
 * for first, since it sends the profile to a model. A copy sends nothing and goes straight through.
 */
export function useNewBaseCv() {
  const client = useQueryClient();
  const refresh = useRefreshBaseCvs();
  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
  });
  // The build that waits for the person's agreement.
  const [asking, setAsking] = useState<BaseCvRequest>();
  const [creating, setCreating] = useState(false);

  const create = useMutation({
    mutationFn: createBaseCv,
    onSuccess: () => refresh(),
    onError: (error) => {
      toast.error(failureSentence(error));
      void refresh();
    },
  });
  const agree = useMutation({
    mutationFn: async (version: number) => {
      const accepted = await acceptConsent(version);
      client.setQueryData(consentKey, accepted);
      return accepted;
    },
    // A 422 means the text changed under the open tab: read the current one to agree to.
    onError: () => {
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });

  /** A build from the profile needs the consent; anything else goes straight through. */
  const start = (request: BaseCvRequest) => {
    if (request.from === "profile" && consent.data?.accepted !== true) {
      if (consent.data === undefined) {
        toast.error(
          "What is sent could not be loaded yet. Try again in a moment.",
        );
        return;
      }
      setCreating(false);
      setAsking(request);
      return;
    }
    create.mutate(request, {
      onSuccess: () => {
        setCreating(false);
      },
    });
  };

  const agreeAndBuild = (version: number) => {
    const request = asking;
    if (request === undefined) return;
    agree.mutate(version, {
      onSuccess: () => {
        setAsking(undefined);
        setCreating(false);
        create.mutate(request);
      },
    });
  };

  const closeConsent = () => {
    agree.reset();
    setAsking(undefined);
  };

  return {
    consent,
    /** The build waiting for the consent dialog, if any. */
    asking,
    /** The "New CV" dialog is open. */
    creating,
    setCreating,
    create,
    agree,
    start,
    agreeAndBuild,
    closeConsent,
  };
}
