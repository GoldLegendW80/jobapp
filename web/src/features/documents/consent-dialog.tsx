import { LoaderCircle } from "lucide-react";
import { useId, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import type { Consent, Estimate } from "./documents-api";
import { money, tokenRange } from "./labels";

const CHECKBOX = "mt-0.5 size-4 shrink-0 accent-primary";

function figure(estimate: Estimate): string {
  const range = tokenRange(estimate.tokens);
  return estimate.cost === null
    ? range
    : `${range}, about ${money(estimate.cost.currency, estimate.cost.low)} to ${money(estimate.cost.currency, estimate.cost.high)}`;
}

/**
 * What is sent, to whom, and what it costs, the first time a CV is built from the profile (and
 * again when the text changes). The box is ticked for the version shown, never a bare yes; the
 * button records exactly that version. Nothing is sent to a model before it.
 */
export function ConsentDialog({
  consent,
  open,
  busy,
  error,
  onAgree,
  onClose,
  title = "What is sent to build your CV",
  agreeLabel = "Agree and build my CV",
  onCloseAutoFocus,
}: {
  consent: Consent;
  open: boolean;
  busy: boolean;
  error?: string;
  /** Called with the version of the text on screen. */
  onAgree: (version: number) => void;
  onClose: () => void;
  title?: string;
  agreeLabel?: string;
  onCloseAutoFocus?: (event: Event) => void;
}) {
  const boxId = useId();
  // The version the box was ticked for: a text that changed under the dialog needs a new tick.
  const [tickedVersion, setTickedVersion] = useState<number | null>(null);
  const ticked = tickedVersion === consent.version;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent
        className="max-h-[90vh] overflow-y-auto break-words sm:max-w-lg"
        onCloseAutoFocus={onCloseAutoFocus}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Read this once. Nothing is sent to a model before you agree.
          </DialogDescription>
        </DialogHeader>

        <section className="flex flex-col gap-2 text-sm">
          <h4 className="font-semibold">What is sent, and to whom</h4>
          <p data-testid="consent-text">{consent.text}</p>
          <p className="text-muted-foreground">Version {consent.version}</p>
          <p className="text-muted-foreground" data-testid="consent-model">
            {consent.model === null
              ? "No model credential is saved yet: the CV waits until you add one in your account."
              : `Model: ${consent.model}.`}
          </p>
        </section>

        <section
          className="flex flex-col gap-2 text-sm"
          data-testid="consent-estimates"
        >
          <h4 className="font-semibold">What it costs</h4>
          <ul className="flex flex-col gap-1">
            <li>One posting: {figure(consent.posting)}.</li>
            <li>
              A typical week at your threshold of {consent.min_fit}:{" "}
              {consent.week.postings} postings, {figure(consent.week)}.
            </li>
          </ul>
          <p className="text-muted-foreground">{consent.cost_note}</p>
        </section>

        {error !== undefined ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <label
          htmlFor={boxId}
          className="flex items-start gap-3 text-sm font-medium"
        >
          <input
            id={boxId}
            type="checkbox"
            className={CHECKBOX}
            checked={ticked}
            disabled={busy}
            onChange={(event) => {
              setTickedVersion(event.target.checked ? consent.version : null);
            }}
          />
          I have read this and I agree to send it
        </label>

        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            Not now
          </Button>
          <Button
            className="whitespace-normal"
            disabled={!ticked || busy}
            onClick={() => {
              onAgree(consent.version);
            }}
          >
            {busy ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            {agreeLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
