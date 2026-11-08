import { useId, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { describeError } from "@/lib/api";

import { useMailDraft, useSendMailDraft } from "@/features/mail/api/drafts-api";
import type { FollowUp, FollowUpDraft } from "../api/followups-api";
import { useFollowUpToGmail, useRefreshFollowUps } from "../api/use-followups";

/**
 * Send with Gmail (SPEC-mail.md, "Drafting and sending"): the follow up becomes a Gmail draft to
 * the address the person types, then the mail gate's own confirmation: the recipient and each
 * claim no source states, ticked by the person, and Send, the person's click on that exact
 * draft. The gate refuses the assistant's text while it holds such a claim: the person edits it
 * first, which makes it theirs.
 */
export function FollowUpGmail({
  item,
  draft,
  open,
  onOpenChange,
}: {
  item: FollowUp;
  draft: FollowUpDraft;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const id = useId();
  const [to, setTo] = useState("");
  const [mailId, setMailId] = useState<string | null>(draft.mail_draft_id);
  const [confirmed, setConfirmed] = useState(false);
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const prepare = useFollowUpToGmail(item.posting_id);
  const mail = useMailDraft(open ? mailId : null);
  const refresh = useRefreshFollowUps();
  const send = useSendMailDraft((result) => {
    if (result.result === "sent") {
      toast.success("Sent with Gmail.");
      refresh();
      onOpenChange(false);
    } else {
      toast.error(result.detail ?? "We could not confirm this was sent.");
    }
  });
  const shown = mail.data;
  const issues = shown?.issues ?? [];
  const agentText = draft.written_by === "agent" && issues.length > 0;
  const ready =
    shown !== undefined &&
    shown.status === "draft" &&
    confirmed &&
    !agentText &&
    issues.every((issue) => ticked.has(issue.id));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send with Gmail</DialogTitle>
          <DialogDescription>
            Your follow up to {item.company} goes from your Gmail when you click
            Send. Nothing is sent before.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            prepare.mutate(to, {
              onSuccess: (made) => {
                setMailId(made.draft_id);
                setConfirmed(false);
                setTicked(new Set());
              },
            });
          }}
        >
          <Field>
            <FieldLabel htmlFor={`${id}-to`}>To</FieldLabel>
            <Input
              id={`${id}-to`}
              type="email"
              value={to}
              onChange={(event) => {
                setTo(event.target.value);
              }}
              placeholder="the address of the recruiter or the company"
            />
          </Field>
          {prepare.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {describeError(prepare.error)}
            </p>
          ) : null}
          <Button
            type="submit"
            variant="outline"
            disabled={!to.trim() || prepare.isPending}
          >
            Prepare in Gmail
          </Button>
        </form>
        {shown ? (
          <div className="flex flex-col gap-3 text-sm">
            <p className="font-medium">{shown.subject}</p>
            <p className="whitespace-pre-line">{shown.body}</p>
            {shown.to.map((address) => (
              <Field key={address} orientation="horizontal" className="gap-2">
                <Checkbox
                  id={`${id}-to-${address}`}
                  checked={confirmed}
                  onCheckedChange={(value) => {
                    setConfirmed(value === true);
                  }}
                />
                <FieldLabel htmlFor={`${id}-to-${address}`}>
                  Send to {address}
                </FieldLabel>
              </Field>
            ))}
            {agentText ? (
              <p role="alert" className="text-destructive">
                The assistant&apos;s text names something no source in your
                profile states. Edit the text, save it, and prepare it again.
              </p>
            ) : (
              issues.map((issue) => (
                <Field
                  key={issue.id}
                  orientation="horizontal"
                  className="gap-2"
                >
                  <Checkbox
                    id={`${id}-${issue.id}`}
                    checked={ticked.has(issue.id)}
                    onCheckedChange={(value) => {
                      setTicked((old) => {
                        const next = new Set(old);
                        if (value === true) next.add(issue.id);
                        else next.delete(issue.id);
                        return next;
                      });
                    }}
                  />
                  <FieldLabel htmlFor={`${id}-${issue.id}`}>
                    I checked “{shown.body.slice(issue.start, issue.end)}”
                  </FieldLabel>
                </Field>
              ))
            )}
            {shown.message ? <p>{shown.message}</p> : null}
            {send.isError ? (
              <p role="alert" className="text-destructive">
                {describeError(send.error)}
              </p>
            ) : null}
          </div>
        ) : null}
        <DialogFooter>
          <Button
            disabled={!ready || send.isPending}
            onClick={() => {
              if (shown)
                send.mutate({ draft: shown, acknowledged: [...ticked] });
            }}
          >
            Send
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
