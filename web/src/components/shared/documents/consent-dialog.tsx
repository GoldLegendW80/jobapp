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

import type { Consent, Estimate } from "@/features/documents/api/consent-api";
import { money, tokenRange } from "@/features/documents/helpers/labels";

function figure(estimate: Estimate): string {
  const range = tokenRange(estimate.tokens);
  return estimate.cost === null
    ? range
    : `${range}, about ${money(estimate.cost.currency, estimate.cost.low)} to ${money(estimate.cost.currency, estimate.cost.high)}`;
}

/** The consent of a Fill my profile run, asked in the same dialog (`run` below). */
export interface RunConsent {
  version: number;
  text: string;
}

/**
 * What is sent, to whom, and what it costs, the first time a CV is built from the profile (and
 * again when the text changes). The box is ticked for the version shown, never a bare yes; the
 * button records exactly that version. Nothing is sent to a model before it.
 *
 * With `run`, the same dialog also asks the consent of the Fill my profile run that completes
 * the profile from this CV (owner, 2026-11-05: one dialog, two boxes, one button). Each box is
 * ticked for its own text and version; the run's box may stay empty, and the run asks again
 * when it starts. The button hands both versions over: each consent is recorded on its own.
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
  run,
}: {
  consent: Consent;
  open: boolean;
  busy: boolean;
  error?: string;
  /** Called with the version of the text on screen, and of the run's when its box is ticked. */
  onAgree: (version: number, runVersion: number | null) => void;
  onClose: () => void;
  title?: string;
  agreeLabel?: string;
  onCloseAutoFocus?: (event: Event) => void;
  run?: RunConsent;
}) {
  const boxId = useId();
  const runBoxId = useId();
  const [runTicked, setRunTicked] = useState<number | null>(null);
  const runAgreed = run !== undefined && runTicked === run.version;
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
            <li>
              One posting, its CV and letter read by the hiring panel:{" "}
              {figure(consent.posting)}.
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

        {run ? (
          <section
            className="flex flex-col gap-2 border-t pt-4 text-sm"
            data-testid="run-consent"
          >
            <h4 className="font-semibold">To complete your profile from it</h4>
            <p data-testid="run-consent-text">{run.text}</p>
            <p className="text-muted-foreground">Version {run.version}</p>
            <Field orientation="horizontal" className="items-start">
              <Checkbox
                id={runBoxId}
                className="mt-0.5"
                checked={runAgreed}
                disabled={busy}
                onCheckedChange={(checked) => {
                  setRunTicked(checked === true ? run.version : null);
                }}
              />
              <FieldLabel htmlFor={runBoxId}>
                I agree to send this CV to complete my profile
              </FieldLabel>
            </Field>
            <p className="text-muted-foreground">
              You may leave this one empty: the app asks again when you complete
              your profile.
            </p>
          </section>
        ) : null}

        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            Not now
          </Button>
          <Button
            className="whitespace-normal"
            disabled={!ticked || busy}
            onClick={() => {
              onAgree(consent.version, runAgreed ? run.version : null);
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
