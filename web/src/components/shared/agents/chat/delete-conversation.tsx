import { Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { describeError, errorDetail } from "@/lib/api";

import { useDeleteJobThread } from "@/features/agents/api/use-job-thread";

/** The bar above a conversation: Delete conversation, then Delete or Keep. */
export function DeleteConversation({
  jobId,
  threadId,
  onDeleted,
}: {
  jobId: string;
  threadId: string;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteJobThread(jobId, threadId, {
    onSuccess: () => {
      setConfirming(false);
      onDeleted();
      toast.info("Conversation deleted.");
    },
    onError: (error) => {
      toast.error(errorDetail(error) ?? describeError(error));
    },
  });
  return (
    <div className="flex items-center justify-end gap-2 border-b px-4 py-2">
      {confirming ? (
        <>
          <span className="mr-auto text-sm">Delete this conversation?</span>
          <Button
            variant="destructive"
            size="sm"
            disabled={remove.isPending}
            aria-busy={remove.isPending}
            onClick={() => {
              remove.mutate();
            }}
          >
            Delete
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setConfirming(false);
            }}
          >
            Keep
          </Button>
        </>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setConfirming(true);
          }}
        >
          <Trash2 aria-hidden="true" />
          Delete conversation
        </Button>
      )}
    </div>
  );
}
