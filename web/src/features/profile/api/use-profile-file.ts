import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { describeError } from "@/lib/api";

import { conflictOf, profileKey, type Profile } from "./profile-api";
import { exportProfile, importProfile } from "./profile-file-api";
import { downloadJson } from "../helpers/download";
import { exportFileName } from "../helpers/import-file";

/** Downloads the profile as profile-YYYY-MM-DD.json, and says how it went. */
export function useExportProfile() {
  return useMutation({
    mutationFn: exportProfile,
    onSuccess: (file) => {
      const name = exportFileName(file.exported_at);
      downloadJson(file, name);
      toast.success(`Profile exported as ${name}.`);
    },
    onError: (error) => {
      toast.error(`The profile was not exported. ${describeError(error)}`);
    },
  });
}

/**
 * Imports a profile file over the profile at `revision`. The saved profile goes to the cache; on a
 * 409 the cache takes the current revision and sections, and `onStale` follows.
 */
export function useImportProfile(
  revision: number,
  on: {
    onStart: () => void;
    onImported: () => void;
    onStale: () => void;
    onRefused: (error: Error) => void;
  },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (document: Record<string, unknown>) =>
      importProfile(document, revision),
    onMutate: () => {
      on.onStart();
    },
    onSuccess: (saved) => {
      client.setQueryData(profileKey, saved);
      void client.invalidateQueries({ queryKey: ["profile", "revisions"] });
      on.onImported();
    },
    onError: (error) => {
      const current = conflictOf(error);
      if (current) {
        client.setQueryData<Profile>(profileKey, (old) =>
          old
            ? { ...old, revision: current.revision, sections: current.sections }
            : old,
        );
        on.onStale();
        return;
      }
      on.onRefused(error);
    },
  });
}
