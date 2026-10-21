import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";

import { refusalOf } from "./apply-api";
import { answerRehearsal } from "./queue-api";

/**
 * A Full auto rehearsal's question (SPEC-apply.md, "Modes", Dry run): the form was filled and
 * checked, nothing was sent. Yes sends it to the queue, ready for a one click Approve, and counts
 * one rehearsal done; No changes nothing else, so Full auto stays in rehearsal. `onLeave` is
 * called when a No took the item out of the queue.
 */
export function RehearsalQuestion({
  applicationId,
  onLeave,
}: {
  applicationId: string;
  onLeave: () => void;
}) {
  const queryClient = useQueryClient();
  const id = useId();
  const [notice, setNotice] = useState<string | null>(null);
  const answer = useMutation({
    mutationFn: (yes: boolean) =>
      answerRehearsal(applicationId, yes ? "yes" : "no"),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (_, yes) => {
      if (yes) {
        toast.success(
          "Thank you. It waits in your queue, ready for your approval.",
        );
      } else {
        toast.success(
          "Noted. Nothing was sent, and your next Like is a rehearsal again.",
        );
        onLeave();
      }
    },
    onError: (error) => {
      setNotice(refusalOf(error)?.detail ?? describeError(error));
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["apply"] });
    },
  });

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex flex-col gap-2 rounded-md border border-sky-300 bg-sky-50 p-3 text-sm dark:border-sky-700 dark:bg-sky-950"
    >
      <h4 id={`${id}-title`} className="font-semibold">
        Does this look right?
      </h4>
      <p>
        A rehearsal of Full auto: the app filled this form and checked it, and
        sent nothing. Yes keeps it in your queue, ready for your approval.
      </p>
      {notice ? (
        <Alert variant="destructive">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button
          className="w-full sm:w-fit"
          disabled={answer.isPending}
          aria-busy={answer.isPending && answer.variables}
          onClick={() => {
            answer.mutate(true);
          }}
        >
          {answer.isPending && answer.variables ? (
            <LoaderCircle aria-hidden="true" className="animate-spin" />
          ) : null}
          Yes, it looks right
        </Button>
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={answer.isPending}
          onClick={() => {
            answer.mutate(false);
          }}
        >
          No, something is wrong
        </Button>
      </div>
    </section>
  );
}
