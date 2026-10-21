import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { LiveBrowser } from "@/features/browser/live-browser";
import { describeError } from "@/lib/api";

import { refusalOf, type Application } from "./apply-api";
import { APPLY_POLL_MS } from "./apply-state";
import { ConfirmDialog, type Confirmation } from "./confirm-dialog";
import {
  actManual,
  closeManual,
  getManual,
  manualKey,
  openManual,
  recordManual,
  type ManualAction,
} from "./manual-api";
import { busyLine, outcomeLine, REOPEN_MS } from "./manual-state";

const OPEN_STATES = new Set(["queued", "filling", "needs_person"]);

const ASKS = {
  sent: {
    title: "Record this application as sent?",
    sentence:
      "Only do this if you clicked the form's own Submit button. The posting becomes Applied.",
    confirm: "Record as sent",
  },
  close: {
    title: "Close your browser without recording it?",
    sentence:
      "The application is not recorded as sent. If you submitted the form, record it instead.",
    confirm: "Close without submitting",
  },
} satisfies Record<string, Confirmation>;
type Ask = keyof typeof ASKS;

/**
 * Apply myself (SPEC-apply.md, "Modes, and who clicks Submit"; apply A9): the form opens in the
 * person's browser, shown live here with the keyboard and the mouse theirs. The panel asks the app
 * to fill identity fields or attach the CV or the letter, warns before the idle timeout, and
 * records the application once the person sent it. The app never clicks the form's Submit here.
 */
export function ManualPanel({
  application,
  onChanged,
  onFailed,
}: {
  application: Application;
  onChanged: (changed?: Application) => void;
  onFailed: (error: unknown) => void;
}) {
  const queryClient = useQueryClient();
  const id = application.id;
  const open = OPEN_STATES.has(application.state);
  const [asking, setAsking] = useState<Ask | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const status = useQuery({
    queryKey: manualKey(id),
    queryFn: ({ signal }) => getManual(id, signal),
    enabled: open,
    refetchInterval: open ? APPLY_POLL_MS : false,
  });
  const manual = status.data;

  // A queued application asks for its session, and asks again while it waits for the browser.
  const queued = application.state === "queued";
  useEffect(() => {
    if (!queued) return;
    const ask = () => {
      openManual(id).then(
        (found) => queryClient.setQueryData(manualKey(id), found),
        (error: unknown) => {
          setNotice(refusalOf(error)?.detail ?? describeError(error));
        },
      );
    };
    ask();
    const timer = window.setInterval(ask, REOPEN_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [queued, id, queryClient]);

  const act = useMutation({
    mutationFn: (action: ManualAction) => actManual(id, action),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (found) => {
      queryClient.setQueryData(manualKey(id), found);
    },
    onError: (error) => {
      setNotice(refusalOf(error)?.detail ?? describeError(error));
    },
  });
  const finish = useMutation({
    mutationFn: (how: "seen" | Ask) =>
      how === "close" ? closeManual(id) : recordManual(id, how === "seen"),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (changed) => {
      setAsking(null);
      onChanged(changed);
    },
    onError: (error) => {
      setAsking(null);
      onFailed(error);
    },
  });

  if (application.state === "submitted") {
    return (
      <p className="text-sm text-muted-foreground">
        You sent this form yourself. The app cannot know which files you
        uploaded: it kept the versions it placed in your browser.
      </p>
    );
  }
  if (!open) return null;

  const live = application.state === "filling" && manual?.phase === "open";
  const busy = act.isPending || finish.isPending || Boolean(manual?.busy);
  const placed = new Set(manual?.documents ?? []);
  const lines = [
    busyLine(manual?.busy),
    manual?.busy ? null : outcomeLine(manual?.last ?? null),
  ].filter((line): line is string => line !== null);

  return (
    <section
      aria-label="Your session to send it yourself"
      data-testid="manual-panel"
      className="flex flex-col gap-3"
    >
      {manual?.warnings.map((warning) => (
        <Alert key={warning.code}>
          <AlertDescription>
            {warning.sentence} The app will not send anything: you decide.
          </AlertDescription>
        </Alert>
      ))}
      {notice ? (
        <Alert variant="destructive">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}

      {live ? (
        <LiveBrowser person />
      ) : (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          {manual?.phase === "waiting" && manual.position
            ? `Waiting for your browser, place ${manual.position} in line.`
            : "Opening your browser."}
        </p>
      )}

      {manual?.idle_warning ? (
        <Alert>
          <AlertDescription>
            {`Your browser closes in ${manual.idle_left_s ?? 60} seconds unless you click or type in it.`}
          </AlertDescription>
        </Alert>
      ) : null}
      {manual?.chooser ? (
        <p role="status" className="text-sm">
          The form asks for a file. Click Attach my CV or Attach my letter to
          give it one.
        </p>
      ) : null}
      {lines.map((line) => (
        <p key={line} role="status" className="text-sm text-muted-foreground">
          {line}
        </p>
      ))}

      {live ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            disabled={busy}
            onClick={() => {
              act.mutate("fill_details");
            }}
          >
            Fill my details
          </Button>
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            disabled={busy || !placed.has("cv")}
            onClick={() => {
              act.mutate("attach_cv");
            }}
          >
            Attach my CV
          </Button>
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            disabled={busy || !placed.has("letter")}
            onClick={() => {
              act.mutate("attach_letter");
            }}
          >
            Attach my letter
          </Button>
        </div>
      ) : null}

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {live && manual.confirmation_seen ? (
          <Button
            className="w-full sm:w-fit"
            disabled={finish.isPending}
            onClick={() => {
              finish.mutate("seen");
            }}
          >
            Record as applied
          </Button>
        ) : null}
        {live ? (
          <Button
            variant={manual.confirmation_seen ? "outline" : "default"}
            className="w-full sm:w-fit"
            disabled={finish.isPending}
            onClick={() => {
              setAsking("sent");
            }}
          >
            I submitted it
          </Button>
        ) : null}
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={finish.isPending}
          onClick={() => {
            setAsking("close");
          }}
        >
          Close without submitting
        </Button>
      </div>
      {live && manual.confirmation_seen ? (
        <p className="text-sm text-muted-foreground">
          The page shows a confirmation: record it to mark the posting as
          applied.
        </p>
      ) : null}

      <ConfirmDialog
        ask={asking ? ASKS[asking] : null}
        pending={finish.isPending}
        onCancel={() => {
          setAsking(null);
        }}
        onConfirm={() => {
          if (asking) finish.mutate(asking);
        }}
      />
    </section>
  );
}
