import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import {
  getRun,
  isActive,
  refreshRuns,
  runKey,
  RUN_POLL_MS,
} from "@/features/ingestion/api/runs-api";

import { FILL_REVIEW, REVIEW_OPENED } from "../helpers/upload-cv";

/**
 * The Fill my profile run started from an uploaded CV, read while it runs. When it ends, its
 * review opens by itself, once, and the person is told.
 */
export function useFollowProfileRun() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [following, setFollowing] = useState<string>();
  const run = useQuery({
    queryKey: runKey(following ?? "none"),
    queryFn: ({ signal }) => getRun(following ?? "", signal),
    enabled: following !== undefined,
    refetchInterval: (current) =>
      current.state.data && !isActive(current.state.data.status)
        ? false
        : RUN_POLL_MS,
  });
  const ended =
    following !== undefined &&
    run.data?.id === following &&
    !isActive(run.data.status);
  const opened = useRef<string>(undefined);
  useEffect(() => {
    if (!ended || opened.current === following) return;
    opened.current = following;
    refreshRuns(client);
    toast.info(REVIEW_OPENED);
    void navigate(FILL_REVIEW);
  }, [ended, following, client, navigate]);
  return { following, follow: setFollowing };
}
