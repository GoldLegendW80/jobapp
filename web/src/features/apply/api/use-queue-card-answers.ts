import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { approvalKey, getApproval } from "./apply-api";
import type { QueueItem } from "./queue-api";

/**
 * The answers of one queue card: the filled form read when the card shows it (read again when the
 * state moves), whether the answers are unfolded, and how many are typed and not saved, told to
 * the page (`onUnsavedChange`) so Approve all ready leaves the card out.
 */
export function useQueueCardAnswers(
  item: QueueItem,
  withAnswers: boolean,
  onUnsavedChange?: (id: string, count: number) => void,
) {
  const queryClient = useQueryClient();
  // Until the person toggles it, the answers are open once a required one was seen left, and
  // stay open after it is answered: the person may still be on the optional ones.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const [requiredSeen, setRequiredSeen] = useState(item.required_left > 0);
  if (item.required_left > 0 && !requiredSeen) setRequiredSeen(true);
  const open = toggled ?? requiredSeen;

  const [unsaved, setUnsaved] = useState(0);
  useEffect(() => {
    onUnsavedChange?.(item.id, unsaved);
  }, [onUnsavedChange, item.id, unsaved]);
  useEffect(
    () => () => {
      onUnsavedChange?.(item.id, 0);
    },
    [onUnsavedChange, item.id],
  );

  const approvalQuery = useQuery({
    queryKey: approvalKey(item.id),
    queryFn: ({ signal }) => getApproval(item.id, signal),
    enabled: withAnswers,
  });

  // The state moved (the fill ended, another tab decided): read the form again.
  const seen = useRef(item.state);
  useEffect(() => {
    if (seen.current === item.state) return;
    seen.current = item.state;
    void queryClient.invalidateQueries({ queryKey: approvalKey(item.id) });
  }, [item.state, item.id, queryClient]);

  return {
    approvalQuery,
    approval: approvalQuery.data,
    open,
    setOpen: setToggled,
    unsaved,
    setUnsaved,
  };
}
