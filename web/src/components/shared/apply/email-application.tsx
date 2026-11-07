import { Button } from "@/components/ui/button";
import { useEmailApplication } from "@/features/apply/api/use-email-application";
import { useReconcileSend } from "@/features/mail/api/use-reconcile-send";
import { describeError } from "@/lib/api";

/** The complete message is visible beside the document versions before approval. */
export function EmailApplication({ applicationId }: { applicationId: string }) {
  const draft = useEmailApplication(applicationId);
  const check = useReconcileSend(draft.data?.send_id);
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
        <p role="status" className="text-sm text-warning">
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
