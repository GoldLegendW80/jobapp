import { ClipboardPaste, ListTree } from "lucide-react";
import { useId, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { describeError } from "@/lib/api";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { useSetPostingText } from "@/features/jobs/api/use-posting-text";
import { isListed } from "@/features/jobs/helpers/listed-words";
import { ListedMention } from "../listed-mention";

/** The api's bound of a posting's text (sweep.POSTING_TEXT_MAX). */
export const POSTING_TEXT_MAX = 200_000;

/**
 * Jobs J31, on a posting seen only in a list (the posting page and the Jobs list's pane): the
 * mention with its links, what waits for the posting's text, and "Paste its text", which replaces
 * the card's text with the person's (jobs J31b). Nothing for any other posting.
 */
export function ListedNotice({ job }: { job: JobDetail }) {
  const [open, setOpen] = useState(false);
  if (!isListed(job)) return null;
  return (
    <Alert variant="warning" data-testid="listed-notice">
      <ListTree aria-hidden="true" />
      <AlertTitle>Its text is the card's of a list</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <ListedMention job={job} className="text-sm" />
        <p>
          Its fit is less reliable, and no CV, Like or application is made until
          its text is read: a later search that reads its own page replaces it,
          or you can paste it yourself.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="w-fit"
          onClick={() => {
            setOpen(true);
          }}
        >
          <ClipboardPaste aria-hidden="true" />
          Paste its text
        </Button>
      </AlertDescription>
      {open ? (
        <PasteTextDialog
          job={job}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </Alert>
  );
}

function PasteTextDialog({
  job,
  onClose,
}: {
  job: JobDetail;
  onClose: () => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const save = useSetPostingText(job.id, { onSuccess: onClose });
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (text.trim()) save.mutate(text);
          }}
        >
          <DialogHeader>
            <DialogTitle>Paste its text</DialogTitle>
            <DialogDescription>
              Copy the whole posting from its own page and paste it here. It
              replaces the card&apos;s text, the fit is computed again and the
              mention goes. Your unwanted titles and places and your never apply
              rules apply to it as to any posting found.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor={`${id}-text`}>
              The text of {job.title} at {job.company}
            </Label>
            <Textarea
              id={`${id}-text`}
              value={text}
              maxLength={POSTING_TEXT_MAX}
              rows={12}
              className="max-h-[50dvh] [overflow-wrap:anywhere]"
              onChange={(event) => {
                setText(event.target.value);
                if (save.isError) save.reset();
              }}
            />
          </div>
          {save.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {describeError(save.error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={save.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending || !text.trim()}>
              {save.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Use this text
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
