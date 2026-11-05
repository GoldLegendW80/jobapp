import { useId, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Spinner } from "@/components/ui/spinner";

import type { Consent, Estimate } from "./documents-api";
import { money, tokenRange } from "./labels";

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

        <Field orientation="horizontal" className="items-start">
          <Checkbox
            id={boxId}
            className="mt-0.5"
            checked={ticked}
            disabled={busy}
            onCheckedChange={(checked) => {
              setTickedVersion(checked === true ? consent.version : null);
            }}
          />
          <FieldLabel htmlFor={boxId}>
            I have read this and I agree to send it
          </FieldLabel>
        </Field>

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
            {busy ? <Spinner aria-hidden="true" role={undefined} /> : null}
            {agreeLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
