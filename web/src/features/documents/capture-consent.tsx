import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { isApiError } from "@/lib/api";

import { ConsentDialog } from "./consent-dialog";
import {
  acceptConsent,
  baseCvsKey,
  consentKey,
  documentKey,
  getConsent,
  type Consent,
} from "./documents-api";

const TITLE = "What is sent to resume your documents";
const AGREE = "Agree and resume waiting documents";

/** Consent resumes waiting generations on the server; this action creates no new run. */
export function CaptureConsent({ documentId }: { documentId: string }) {
  const client = useQueryClient();
  const trigger = useRef<HTMLButtonElement>(null);
  const read = useRef<AbortController>();
  // A ref also guards activations before React renders the pending state.
  const posting = useRef(false);
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<Consent>();
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  useEffect(() => () => read.current?.abort(), []);

  const load = async () => {
    read.current?.abort();
    const controller = new AbortController();
    read.current = controller;
    setCurrent(undefined);
    setError(undefined);
    setLoading(true);
    try {
      const consent = await getConsent(controller.signal);
      if (!controller.signal.aborted) setCurrent(consent);
    } catch {
      if (!controller.signal.aborted)
        setError("What is sent could not be loaded. Try again.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };
  const close = () => {
    if (posting.current) return;
    read.current?.abort();
    setOpen(false);
    setCurrent(undefined);
  };
  const returnFocus = (event: Event) => {
    event.preventDefault();
    trigger.current?.focus();
  };

  const agree = async (version: number) => {
    if (posting.current || current?.version !== version) return;
    posting.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const accepted = await acceptConsent(version);
      await client.cancelQueries({ queryKey: consentKey });
      client.setQueryData(consentKey, accepted);
      setOpen(false);
      setCurrent(undefined);
      await Promise.all([
        client.invalidateQueries({ queryKey: baseCvsKey }),
        client.invalidateQueries({ queryKey: documentKey(documentId) }),
      ]);
    } catch (failure) {
      if (isApiError(failure) && failure.status === 422) {
        posting.current = false;
        setBusy(false);
        await load();
      } else {
        setError("Your agreement could not be saved. Try again.");
      }
    } finally {
      posting.current = false;
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        ref={trigger}
        type="button"
        variant="link"
        size="sm"
        className="h-auto self-start whitespace-normal text-left"
        onClick={() => {
          if (open) return;
          setOpen(true);
          void load();
        }}
      >
        Confirm what is sent
      </Button>
      {open && current !== undefined ? (
        <ConsentDialog
          consent={current}
          open
          busy={busy}
          error={error}
          title={TITLE}
          agreeLabel={AGREE}
          onClose={close}
          onCloseAutoFocus={returnFocus}
          onAgree={(version) => void agree(version)}
        />
      ) : null}
      <Dialog
        open={open && current === undefined}
        onOpenChange={(next) => {
          if (!next) close();
        }}
      >
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (!open) trigger.current?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>{TITLE}</DialogTitle>
            <DialogDescription>
              Read the current text before you agree.
            </DialogDescription>
          </DialogHeader>
          {loading ? <p role="status">Loading what is sent</p> : null}
          {error !== undefined ? (
            <>
              <p role="alert">{error}</p>
              <Button variant="outline" onClick={() => void load()}>
                Retry loading what is sent
              </Button>
            </>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              Not now
            </Button>
            <Button disabled className="whitespace-normal">
              {AGREE}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
