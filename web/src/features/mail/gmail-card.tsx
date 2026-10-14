import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, Info, LoaderCircle } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { message } from "@/features/admin/errors";

import {
  disconnectMail,
  getMailAccount,
  type MailAccount,
  mailAccountQueryKey,
  navigation,
  OUTCOME_TEXT,
  readOutcome,
  saveConsent,
  START_PATH,
} from "./account-api";
import { CardSuggestions } from "./card-suggestions";

/** The version of CONSENT_TEXT. The api holds the same number (accounts.CONSENT_VERSION). */
export const CONSENT_VERSION = 1;
export const CONSENT_LABEL = "Let the assistant read my job mails";
export const CONSENT_TEXT =
  "If you agree, the mails that concern your job postings are sent to your own model provider, Anthropic, to match them to a posting, to tell what they say and to prepare reply drafts. Without it the app links mails by plain rules only: no model reads a mail and no draft is prepared. You can change this at any time.";

export const CONSENT_NOT_SAVED =
  "Gmail is connected, but your choice about the assistant could not be saved. Tick the box below.";
export const NOT_REVOKED =
  "Gmail is disconnected here and the stored mails are deleted, but Google did not confirm. Remove the access yourself in your Google account, under Security, then Third party access.";

/** Kept across the trip to Google: the user ticked the consent before leaving. */
const PENDING_CONSENT = "jobapp.gmail.consent";

function rememberConsent(ticked: boolean) {
  try {
    if (ticked)
      window.sessionStorage.setItem(PENDING_CONSENT, String(CONSENT_VERSION));
    else window.sessionStorage.removeItem(PENDING_CONSENT);
  } catch {
    // Without storage the box on the card does the same job after connecting.
  }
}

function takePendingConsent(): boolean {
  try {
    const found = window.sessionStorage.getItem(PENDING_CONSENT);
    window.sessionStorage.removeItem(PENDING_CONSENT);
    return found === String(CONSENT_VERSION);
  } catch {
    return false;
  }
}

function when(value: string | null): string {
  if (!value) return "not yet";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

interface Notice {
  tone: "good" | "calm" | "bad";
  text: string;
}

const STATUS_LABEL: Record<MailAccount["status"], string> = {
  not_connected: "Not connected",
  ok: "Connected",
  needs_reconnect: "Needs reconnecting",
  error: "Sync problem",
  disconnecting: "Disconnecting",
};

/** "Gmail" on the Account page: connect, the connected address, the consent, disconnect. */
export function GmailCard() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  // Back from Google: the word of the address, read once, becomes the first notice.
  const [arrival] = useState(() => readOutcome(params.get("gmail")));
  const [notice, setNotice] = useState<Notice | undefined>(() =>
    arrival
      ? {
          tone:
            arrival === "connected"
              ? "good"
              : arrival === "denied"
                ? "calm"
                : "bad",
          text: OUTCOME_TEXT[arrival],
        }
      : undefined,
  );
  const [connectOpen, setConnectOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [ticked, setTicked] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const handled = useRef(false);
  const consentId = useId();
  const dialogConsentId = useId();

  const query = useQuery({
    queryKey: mailAccountQueryKey,
    queryFn: ({ signal }) => getMailAccount(signal),
  });
  const account = query.data;

  const consent = useMutation({
    mutationFn: (granted: boolean) =>
      saveConsent({ granted, version: CONSENT_VERSION }),
    onSuccess: (saved) => {
      client.setQueryData(mailAccountQueryKey, saved);
    },
  });
  const saveConsentNow = consent.mutate;

  const disconnect = useMutation({
    mutationFn: disconnectMail,
    onSuccess: (done) => {
      setConfirmOpen(false);
      void client.invalidateQueries({ queryKey: mailAccountQueryKey });
      if (done.revoked) {
        setNotice(undefined);
        toast.success("Gmail disconnected.");
      } else setNotice({ tone: "bad", text: NOT_REVOKED });
    },
    onError: (error) => {
      setConfirmOpen(false);
      setNotice({ tone: "bad", text: message(error) });
    },
  });

  // Then the word leaves the address, and a consent ticked before leaving is recorded.
  const hasParam = params.has("gmail");
  useEffect(() => {
    if (!hasParam || handled.current) return;
    handled.current = true;
    const pending = takePendingConsent();
    if (arrival === "connected" && pending)
      saveConsentNow(true, {
        onError: () => {
          setNotice({ tone: "bad", text: CONSENT_NOT_SAVED });
        },
      });
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("gmail");
        return next;
      },
      { replace: true },
    );
  }, [arrival, hasParam, saveConsentNow, setParams]);

  const start = () => {
    rememberConsent(ticked);
    setLeaving(true);
    navigation.go(START_PATH);
  };

  const status = account?.status ?? "not_connected";
  const connected = status !== "not_connected";
  const textChanged =
    account !== undefined &&
    account.consent.current_version !== CONSENT_VERSION;

  return (
    <Card aria-labelledby="gmail-card-title">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="gmail-card-title" className="text-lg">
            Gmail
          </h2>
          {account ? (
            <Badge
              variant={
                status === "ok"
                  ? "default"
                  : status === "not_connected"
                    ? "secondary"
                    : "destructive"
              }
            >
              {STATUS_LABEL[status]}
            </Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          The mails about your job postings, next to each posting. Nothing is
          ever sent without your click.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {notice ? (
          <Alert
            variant={notice.tone === "bad" ? "destructive" : "default"}
            role={notice.tone === "bad" ? "alert" : "status"}
          >
            {notice.tone === "good" ? (
              <CircleCheck aria-hidden="true" />
            ) : notice.tone === "calm" ? (
              <Info aria-hidden="true" />
            ) : (
              <CircleAlert aria-hidden="true" />
            )}
            <AlertDescription>{notice.text}</AlertDescription>
          </Alert>
        ) : null}
        {query.isPending ? (
          <p className="text-sm text-muted-foreground" role="status">
            Loading
          </p>
        ) : query.isError || !account ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{message(query.error)}</AlertDescription>
          </Alert>
        ) : !connected ? (
          <p className="text-sm text-muted-foreground">
            {account.available
              ? "Connect your Gmail to see, on each posting, the mails that belong to it."
              : "Gmail cannot be connected on this server yet."}
          </p>
        ) : (
          <>
            <dl className="grid gap-3 text-sm">
              <div className="grid gap-1">
                <dt className="font-medium">Address</dt>
                <dd className="break-all text-muted-foreground">
                  {account.email}
                </dd>
              </div>
              <div className="grid gap-1">
                <dt className="font-medium">Last sync</dt>
                <dd className="text-muted-foreground">
                  {when(account.last_sync_at)}
                </dd>
              </div>
            </dl>
            {status === "needs_reconnect" ? (
              <p className="text-sm">
                Google no longer accepts this connection. Connect again with the
                same address.
              </p>
            ) : null}
            {status === "error" ? (
              <p className="text-sm">
                The last sync did not work. It is tried again by itself.
              </p>
            ) : null}
            {!account.can_send ? (
              <p className="text-sm">
                Sending is off: it was not allowed at Google. To send replies
                from the app, disconnect and connect again with that permission
                ticked.
              </p>
            ) : null}
            <div className="flex flex-col gap-1">
              <div className="flex items-start gap-2">
                <input
                  id={consentId}
                  type="checkbox"
                  className="mt-0.5 size-4 shrink-0 accent-primary"
                  checked={account.consent.granted}
                  disabled={
                    consent.isPending ||
                    textChanged ||
                    status === "disconnecting"
                  }
                  aria-describedby={`${consentId}-text`}
                  onChange={(event) => {
                    consent.mutate(event.target.checked, {
                      onError: (error) => {
                        setNotice({ tone: "bad", text: message(error) });
                      },
                    });
                  }}
                />
                <label htmlFor={consentId} className="text-sm font-medium">
                  {CONSENT_LABEL}
                </label>
              </div>
              <p
                id={`${consentId}-text`}
                className="text-sm text-muted-foreground"
              >
                {textChanged
                  ? "This text has changed. Reload the page to read it."
                  : `${CONSENT_TEXT} (text version ${String(CONSENT_VERSION)})`}
              </p>
            </div>
            <CardSuggestions />
          </>
        )}
      </CardContent>
      {account ? (
        <CardFooter className="mt-6 flex-col justify-end gap-2 sm:flex-row">
          {status === "not_connected" || status === "needs_reconnect" ? (
            <Button
              type="button"
              className="w-full sm:w-auto"
              disabled={!account.available || leaving}
              onClick={() => {
                if (status === "needs_reconnect") start();
                else setConnectOpen(true);
              }}
            >
              {status === "needs_reconnect" ? "Connect again" : "Connect Gmail"}
            </Button>
          ) : null}
          {connected ? (
            <Button
              type="button"
              variant="outline"
              className="w-full sm:w-auto"
              disabled={disconnect.isPending || status === "disconnecting"}
              onClick={() => {
                setConfirmOpen(true);
              }}
            >
              Disconnect
            </Button>
          ) : null}
        </CardFooter>
      ) : null}

      <Dialog open={connectOpen} onOpenChange={setConnectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Connect Gmail</DialogTitle>
            <DialogDescription>
              You will choose your address at Google and come back here.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3 text-sm">
            <p className="font-medium">What the app reads</p>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              <li>
                For every new mail: the sender, the recipients, the subject and
                Gmail's short preview, to tell whether it concerns one of your
                postings.
              </li>
              <li>
                The whole mail is fetched and stored only when it does. The
                first time, Gmail is searched over the last 180 days.
              </li>
              <li>
                The app never deletes or changes a mail in Gmail, and sends
                nothing without your click.
              </li>
            </ul>
            <p>
              <Link className="underline underline-offset-4" to="/privacy">
                Read the privacy page
              </Link>
            </p>
            <div className="flex flex-col gap-1 rounded-md border p-3">
              <div className="flex items-start gap-2">
                <input
                  id={dialogConsentId}
                  type="checkbox"
                  className="mt-0.5 size-4 shrink-0 accent-primary"
                  checked={ticked}
                  aria-describedby={`${dialogConsentId}-text`}
                  onChange={(event) => {
                    setTicked(event.target.checked);
                  }}
                />
                <label
                  htmlFor={dialogConsentId}
                  className="text-sm font-medium"
                >
                  {CONSENT_LABEL}
                </label>
              </div>
              <p
                id={`${dialogConsentId}-text`}
                className="text-muted-foreground"
              >
                {CONSENT_TEXT} (text version {String(CONSENT_VERSION)})
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              className="w-full sm:w-auto"
              disabled={leaving}
              aria-busy={leaving}
              onClick={start}
            >
              {leaving ? (
                <>
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                  Going to Google
                </>
              ) : (
                "Continue to Google"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect Gmail?</AlertDialogTitle>
            <AlertDialogDescription>
              Deleted from the app: the stored mails, their links to your
              postings, what the assistant drew from them, and the access to
              your mailbox, which is also removed at Google. Kept: your postings
              and their history, which still says that a status changed because
              of a mail, and the log of sent replies, which holds no address and
              no text. Nothing is deleted in Gmail itself.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={disconnect.isPending}>
              Keep it connected
            </AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              disabled={disconnect.isPending}
              aria-busy={disconnect.isPending}
              onClick={() => {
                disconnect.mutate();
              }}
            >
              {disconnect.isPending ? (
                <>
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                  Disconnecting
                </>
              ) : (
                "Disconnect Gmail"
              )}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
