import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { describeError, request } from "@/lib/api";

const emailSchema = z.object({
  id: z.string(),
  status: z.string(),
  to: z.array(z.string()),
  subject: z.string(),
  body: z.string(),
  send_id: z.string().nullable(),
  message: z.string().nullable(),
  issues: z.array(
    z.object({ code: z.string(), start: z.number(), end: z.number() }),
  ),
});

/** The complete message is visible beside the document versions before approval. */
export function EmailApplication({ applicationId }: { applicationId: string }) {
  const client = useQueryClient();
  const draft = useQuery({
    queryKey: ["email-application", applicationId],
    queryFn: () =>
      request(`/applications/${applicationId}/email`, {
        validate: (answer) => emailSchema.parse(answer),
      }),
    refetchInterval: 3000,
    retry: false,
  });
  const check = useMutation({
    mutationFn: () =>
      request(`/mail/sends/${draft.data?.send_id}/reconcile`, {
        method: "POST",
        validate: (answer) => z.object({ resolved: z.number() }).parse(answer),
      }),
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
  if (!draft.data || draft.data.status === "preparing") return null;
  return (
    <section
      aria-label="Application email"
      className="space-y-3 rounded-md border p-4"
    >
      <h3 className="font-medium">Application email</h3>
      <p className="break-all text-sm">
        To:{" "}
        {draft.data.to.join(", ") ||
          "Choose a recipient from the posting below."}
      </p>
      <p className="text-sm">Subject: {draft.data.subject}</p>
      <p className="max-h-80 overflow-auto whitespace-pre-wrap text-sm">
        {draft.data.body}
      </p>
      {draft.data.issues.length > 0 ? (
        <p role="status" className="text-sm text-amber-800 dark:text-amber-300">
          This message needs a fact check. Review the message and edit the email
          answer below before approving.
        </p>
      ) : null}
      {draft.data.status === "send_unknown" ? (
        <div className="space-y-2" role="status">
          <p>We could not confirm this was sent. Look in your Sent folder.</p>
          <p className="text-sm">
            Use “It was submitted” after finding it, or “Try again” to review
            and approve a new send. A second send may duplicate the application.
          </p>
          <Button
            variant="outline"
            disabled={check.isPending}
            onClick={() => {
              check.mutate();
            }}
          >
            Check Sent
          </Button>
          {check.isError ? <p>{describeError(check.error)}</p> : null}
        </div>
      ) : draft.data.message ? (
        <p role="status" className="text-sm">
          {draft.data.message}
        </p>
      ) : null}
    </section>
  );
}
