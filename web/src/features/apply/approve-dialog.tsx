import { useState, type ComponentProps } from "react";
import { LoaderCircle } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

import type { Approval } from "./apply-api";
import { documentLine, leftFields } from "./apply-state";

/**
 * The confirmation before anything is sent: it names the company, the role, the two documents
 * with their versions, and the fields that stay empty. The button sends the hash of the form the
 * person looked at; the api refuses it if the form or the documents changed since.
 */
export function ApproveDialog({
  open,
  onOpenChange,
  company,
  role,
  approval,
  pending,
  onConfirm,
  onCloseAutoFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  company: string;
  role: string;
  approval: Approval;
  pending: boolean;
  onConfirm: (acknowledged?: string[]) => void;
  onCloseAutoFocus?: NonNullable<
    ComponentProps<typeof AlertDialogContent>["onCloseAutoFocus"]
  >;
}) {
  const emailText = (key: string): string => {
    const value = approval.answers.find(
      (answer) => answer.field_key === key,
    )?.value;
    return typeof value === "string" ? value : "";
  };
  const documents = Object.entries(approval.documents);
  const left = leftFields(approval.answers);
  const recipient = approval.answers.find(
    (answer) => answer.field_key === "email_to",
  )?.value;
  const email = approval.answers.some(
    (answer) => answer.field_key === "email_body",
  );
  const [confirmedHash, setConfirmedHash] = useState("");
  const recipientConfirmed = confirmedHash === approval.answers_hash;
  const [acknowledged, setAcknowledged] = useState<Record<string, string>>({});
  const issues = approval.email?.issues ?? [];
  const issuesConfirmed = issues.every(
    (issue) => acknowledged[issue.id] === approval.answers_hash,
  );
  const agentIssues =
    approval.email?.written_by === "agent" && issues.length > 0;
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent {...(onCloseAutoFocus ? { onCloseAutoFocus } : {})}>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Submit this application to {company} for {role}?
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="flex flex-col gap-2 text-left">
              <p>
                {email
                  ? "The app sends this email with the document versions shown here. It cannot be taken back."
                  : "The app fills the form again with exactly the answers you see here, then clicks Submit. It cannot be taken back."}
              </p>
              {email ? (
                <>
                  <p className="break-all">
                    To:{" "}
                    {typeof recipient === "string"
                      ? recipient
                      : "Choose a recipient first."}
                  </p>
                  <p>Subject: {emailText("email_subject")}</p>
                  <p className="max-h-48 overflow-auto whitespace-pre-wrap">
                    {emailText("email_body")}
                  </p>
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={recipientConfirmed}
                      onChange={(event) => {
                        setConfirmedHash(
                          event.currentTarget.checked
                            ? approval.answers_hash
                            : "",
                        );
                      }}
                    />
                    <span>
                      I confirm this new email recipient from the posting:{" "}
                      {typeof recipient === "string" ? recipient : ""}
                    </span>
                  </label>
                  {issues.map((issue) => (
                    <label key={issue.id} className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        disabled={agentIssues}
                        checked={
                          acknowledged[issue.id] === approval.answers_hash
                        }
                        onChange={(event) => {
                          const checked = event.currentTarget.checked;
                          setAcknowledged((old) => ({
                            ...old,
                            [issue.id]: checked ? approval.answers_hash : "",
                          }));
                        }}
                      />
                      <span>
                        I checked this claim against my profile:{" "}
                        {emailText("email_body").slice(issue.start, issue.end)}
                      </span>
                    </label>
                  ))}
                  {agentIssues ? (
                    <p>
                      Edit the email answer before sending: the assistant wrote
                      claims that need your review.
                    </p>
                  ) : null}
                </>
              ) : null}
              {documents.length > 0 ? (
                <ul aria-label="Documents sent" className="list-disc pl-5">
                  {documents.map(([kind, one]) => (
                    <li key={kind}>{documentLine(kind, one)}</li>
                  ))}
                </ul>
              ) : (
                <p>No document is attached.</p>
              )}
              {left.length > 0 ? (
                <p className="font-medium text-amber-800 dark:text-amber-300">
                  Left empty: {left.map((answer) => answer.label).join(", ")}.
                </p>
              ) : null}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Not yet</AlertDialogCancel>
          <Button
            onClick={() => {
              onConfirm(issues.map((issue) => issue.id));
            }}
            disabled={
              pending ||
              (email &&
                (!recipientConfirmed ||
                  typeof recipient !== "string" ||
                  !issuesConfirmed ||
                  agentIssues))
            }
          >
            {pending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            Approve and submit
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
