import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useState } from "react";

import { describeError, errorDetail, isApiError } from "@/lib/api";

import {
  conflictOf,
  profileKey,
  saveSection,
  type Profile,
  type SectionName,
} from "./profile-api";
import {
  getRevision,
  revisionKey,
  revisionsKey,
  type Revision,
} from "./revisions-api";

/** Put a 409's current state in the page cache, as useSectionForm does. */
function applyConflict(client: QueryClient, error: unknown): boolean {
  const current = conflictOf(error);
  if (!current) return false;
  client.setQueryData<Profile>(profileKey, (old) =>
    old
      ? { ...old, revision: current.revision, sections: current.sections }
      : old,
  );
  return true;
}

/**
 * One saved revision of a section and its restore, which saves its data as a new revision based
 * on the revision the page loaded. A save made elsewhere meanwhile answers 409: the page cache
 * takes that version, `onConflict` runs and `stale` asks the person to decide again.
 */
export function useRevisionRestore({
  section,
  revision,
  profile,
  onRestored,
  onConflict,
}: {
  section: SectionName;
  revision: number;
  profile: Profile;
  onRestored: (saved: Profile, from: number) => void;
  onConflict: () => void;
}) {
  const client = useQueryClient();
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string>();
  const version = useQuery({
    queryKey: revisionKey(revision),
    queryFn: ({ signal }) => getRevision(revision, signal),
    // A revision never changes.
    staleTime: Infinity,
  });
  const restore = useMutation({
    mutationFn: (data: Revision["data"]) =>
      saveSection(section, profile.revision, data as never),
    onMutate: () => {
      setError(undefined);
    },
    onSuccess: (saved) => {
      client.setQueryData(profileKey, saved);
      void client.invalidateQueries({ queryKey: revisionsKey(section) });
      onRestored(saved, revision);
    },
    onError: (failure) => {
      if (applyConflict(client, failure)) {
        onConflict();
        void client.invalidateQueries({ queryKey: revisionsKey(section) });
        setStale(true);
        return;
      }
      const detail =
        isApiError(failure) && failure.status === 422
          ? errorDetail(failure)
          : undefined;
      setError(
        detail
          ? `This version cannot be restored as it is: ${detail}`
          : `This version was not restored. ${describeError(failure)}`,
      );
    },
  });

  const restoreVersion = () => {
    if (version.data) restore.mutate(version.data.data);
  };

  return {
    version,
    restoring: restore.isPending,
    restoreVersion,
    stale,
    setStale,
    error,
  };
}
