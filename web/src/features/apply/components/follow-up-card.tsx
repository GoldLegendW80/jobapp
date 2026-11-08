import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { describeError } from "@/lib/api";

import {
  BODY_MAX,
  SUBJECT_MAX,
  type FollowUp,
  type FollowUpDraft,
} from "../api/followups-api";
import {
  useRecordFollowUpSent,
  useSaveFollowUpText,
  useWriteFollowUp,
} from "../api/use-followups";
import { cadenceLine, datesLine, mailtoHref } from "../helpers/follow-up-words";
import { FollowUpGmail } from "./follow-up-gmail";

/**
 * One posting of the follow ups: where its cadence stands, then its draft. The assistant writes
 * a draft only on "Write a draft" (D29); the person edits it, copies it or opens it in their own
 * mail program, or prepares it in Gmail, sends it themselves, then says "I sent it". The app
 * never sends it (hard rule 3).
 */
export function FollowUpCard({
  item,
  today,
  gmail,
}: {
  item: FollowUp;
  today: string;
  gmail: boolean;
}) {
  const write = useWriteFollowUp({
    onError: (error) => toast.error(describeError(error)),
  });
  const sent = useRecordFollowUpSent({
    onSuccess: () => toast.success("Follow up recorded."),
    onError: (error) => toast.error(describeError(error)),
  });
  const draft = item.draft;
  const due = item.urgency === "due";
  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <Link
            to={`/jobs/${item.posting_id}`}
            className="font-medium underline-offset-4 hover:underline"
          >
            {item.title}, {item.company}
          </Link>
          <p className="text-sm">{cadenceLine(item, today)}</p>
          <p className="text-sm text-muted-foreground">{datesLine(item)}</p>
        </div>
        {draft?.writing ? (
          <p role="status" className="flex items-center gap-2 text-sm">
            <Spinner /> The assistant is writing the draft.
          </p>
        ) : draft && draft.body ? (
          <DraftEditor
            key={`${draft.subject}\n${draft.body}`}
            item={item}
            draft={draft}
            gmail={gmail}
            onWrite={() => {
              write.mutate(item.posting_id);
            }}
            onSent={() => {
              sent.mutate(item.posting_id);
            }}
            busy={write.isPending || sent.isPending}
          />
        ) : (
          <div className="flex flex-col gap-2">
            {draft?.error ? (
              <p role="alert" className="text-sm text-destructive">
                {draft.error}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button
                variant={due ? "default" : "outline"}
                disabled={write.isPending}
                onClick={() => {
                  write.mutate(item.posting_id);
                }}
              >
                Write a draft
              </Button>
              <Button
                variant="outline"
                disabled={sent.isPending}
                onClick={() => {
                  sent.mutate(item.posting_id);
                }}
              >
                I sent it
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DraftEditor({
  item,
  draft,
  gmail,
  onWrite,
  onSent,
  busy,
}: {
  item: FollowUp;
  draft: FollowUpDraft;
  gmail: boolean;
  onWrite: () => void;
  onSent: () => void;
  busy: boolean;
}) {
  const id = useId();
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [gmailOpen, setGmailOpen] = useState(false);
  const save = useSaveFollowUpText(item.posting_id, {
    onSuccess: () => toast.success("Draft saved."),
    onError: (error) => toast.error(describeError(error)),
  });
  const edited = subject !== draft.subject || body !== draft.body;
  const mail = mailtoHref(subject, body);
  const copy = async () => {
    try {
      const clipboard = navigator.clipboard as Clipboard | undefined;
      if (!clipboard) throw new Error("no clipboard");
      await clipboard.writeText(`${subject}\n\n${body}`);
      toast.success("Draft copied.");
    } catch {
      toast.error("This browser blocked copying: select the text and copy it.");
    }
  };
  return (
    <div className="flex flex-col gap-3">
      <Field>
        <FieldLabel htmlFor={`${id}-subject`}>Subject</FieldLabel>
        <Input
          id={`${id}-subject`}
          value={subject}
          maxLength={SUBJECT_MAX}
          onChange={(event) => {
            setSubject(event.target.value.replace(/[\r\n]/g, " "));
          }}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor={`${id}-body`}>Message</FieldLabel>
        <Textarea
          id={`${id}-body`}
          value={body}
          maxLength={BODY_MAX}
          rows={8}
          onChange={(event) => {
            setBody(event.target.value);
          }}
        />
      </Field>
      {!edited && draft.issues.length > 0 ? (
        <div
          className="flex flex-col gap-1 text-sm"
          data-testid="follow-up-issues"
        >
          <p>
            Check these before you send: no source in your profile states them.
          </p>
          <ul className="list-disc pl-5">
            {draft.issues.map((issue) => (
              <li key={`${issue.code}:${issue.start}:${issue.end}`}>
                “{draft.body.slice(issue.start, issue.end)}”
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {mail.cut && !edited ? (
        <p className="text-sm text-muted-foreground">
          This message is too long to open whole in your mail: use Copy.
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {edited ? (
          <>
            <Button
              disabled={save.isPending || !subject.trim() || !body.trim()}
              onClick={() => {
                save.mutate({ subject, body });
              }}
            >
              Save
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                setSubject(draft.subject);
                setBody(draft.body);
              }}
            >
              Undo changes
            </Button>
          </>
        ) : (
          <>
            <Button asChild>
              <a href={mail.href}>Open in your mail</a>
            </Button>
            <Button variant="outline" onClick={() => void copy()}>
              Copy
            </Button>
            <Button variant="outline" disabled={busy} onClick={onSent}>
              I sent it
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" aria-label="More actions">
                  More <ChevronDown aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {gmail ? (
                  <DropdownMenuItem
                    onSelect={() => {
                      setGmailOpen(true);
                    }}
                  >
                    Send with Gmail
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem disabled={busy} onSelect={onWrite}>
                  Write again
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
      </div>
      {gmail ? (
        <FollowUpGmail
          item={item}
          draft={draft}
          open={gmailOpen}
          onOpenChange={setGmailOpen}
        />
      ) : null}
    </div>
  );
}
