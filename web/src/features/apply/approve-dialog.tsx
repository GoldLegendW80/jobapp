import type { ComponentProps } from "react";
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
  onConfirm: () => void;
  onCloseAutoFocus: NonNullable<
    ComponentProps<typeof AlertDialogContent>["onCloseAutoFocus"]
  >;
}) {
  const documents = Object.entries(approval.documents);
  const left = leftFields(approval.answers);
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Submit this application to {company} for {role}?
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="flex flex-col gap-2 text-left">
              <p>
                The app fills the form again with exactly the answers you see
                here, then clicks Submit. It cannot be taken back.
              </p>
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
          <Button onClick={onConfirm} disabled={pending}>
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
