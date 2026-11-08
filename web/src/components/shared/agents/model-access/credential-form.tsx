import { CircleAlert, CircleCheck, Eye, EyeOff, Timer } from "lucide-react";
import { useId } from "react";

import { CopyCommand } from "@/components/blocks/copy-command";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { RadioGroup } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";
import { formatWait } from "@/features/auth/helpers/retry-countdown";

import type { ModelCredential } from "@/features/agents/api/agents-api";
import { useCredentialForm } from "@/features/agents/api/use-credential-form";
import { isKind } from "@/features/agents/helpers/model-credential";
import { cn } from "@/lib/utils";
import { CredentialKindOption } from "./credential-kind-option";

/** What the token field shows: a check running, accepted (green), refused (red), or plain. */
export interface CredentialStatus {
  tone: "busy" | "success" | "error" | "none";
  text: string;
}

const STATUS_TEXT: Record<CredentialStatus["tone"], string> = {
  busy: "text-muted-foreground",
  success: "text-success",
  error: "text-destructive",
  none: "text-muted-foreground",
};

/**
 * The form of Model access: which kind of credential, the secret, Save and Remove. The field
 * itself says how the credential stands: a spinner while it is saved or checked, then green
 * when Anthropic accepts it, red with the reason when it refuses it.
 */
export function CredentialForm({
  saved,
  loading,
  status,
  onSaving,
  onSaved,
  onRemove,
}: {
  saved: ModelCredential | null;
  loading: boolean;
  status: CredentialStatus;
  onSaving: () => void;
  onSaved: (value: ModelCredential) => void;
  onRemove: () => void;
}) {
  const form = useCredentialForm({ saved, onSaving, onSaved });
  const ids = { secretHint: useId(), secretError: useId(), status: useId() };
  const { chosen, secretError } = form;
  const secretLabel =
    chosen === "claude_subscription" ? "Subscription token" : "API key";
  const saveLocked = form.saveWait.remaining > 0;
  const shown: CredentialStatus = form.save.isPending
    ? { tone: "busy", text: "Saving" }
    : loading
      ? { tone: "busy", text: "Loading your credential" }
      : status;
  const invalid = Boolean(secretError) || shown.tone === "error";

  return (
    <form
      noValidate
      aria-label="Model access"
      onSubmit={form.submit}
      className="flex max-w-2xl flex-col gap-5"
    >
      {form.formError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{form.formError}</AlertDescription>
        </Alert>
      ) : null}
      <FieldSet className="gap-3">
        <FieldLegend variant="label" className="text-sm font-medium">
          {saved ? "Replace with" : "Credential type"}
        </FieldLegend>
        <RadioGroup
          name="kind"
          className="gap-3"
          value={chosen}
          onValueChange={(value) => {
            if (isKind(value)) form.choose(value);
          }}
        >
          <CredentialKindOption kind="anthropic_api_key">
            Billed to your own Anthropic account. Create a key in the{" "}
            <a
              href="https://console.anthropic.com/settings/keys"
              target="_blank"
              rel="noreferrer noopener"
              className="font-medium text-foreground underline underline-offset-4"
            >
              Anthropic Console
            </a>{" "}
            (console.anthropic.com) and paste it here.
          </CredentialKindOption>
          <CredentialKindOption kind="claude_subscription">
            For the account owner's personal use only: a Claude subscription
            cannot be shared with other people. In a terminal with Claude Code
            installed, run <CopyCommand command="claude setup-token" />, sign
            in, and paste the token it prints.
          </CredentialKindOption>
        </RadioGroup>
      </FieldSet>

      <Field className="gap-2" data-invalid={secretError ? true : undefined}>
        <FieldLabel htmlFor={form.secretId}>{secretLabel}</FieldLabel>
        <InputGroup
          aria-busy={shown.tone === "busy"}
          className={cn(
            shown.tone === "success" &&
              "border-success has-[[data-slot=input-group-control]:focus-visible]:border-success has-[[data-slot=input-group-control]:focus-visible]:ring-success/30",
          )}
        >
          <InputGroupInput
            id={form.secretId}
            name="secret"
            type={form.shown ? "text" : "password"}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            className="font-mono"
            placeholder={
              chosen === "claude_subscription"
                ? "sk-ant-oat01-…"
                : "sk-ant-api03-…"
            }
            value={form.secret}
            maxLength={4096}
            aria-invalid={invalid ? true : undefined}
            aria-describedby={
              secretError
                ? `${ids.status} ${ids.secretHint} ${ids.secretError}`
                : `${ids.status} ${ids.secretHint}`
            }
            onChange={(event) => {
              form.changeSecret(event.target.value);
            }}
          />
          <InputGroupAddon align="inline-end">
            {shown.tone === "busy" ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : shown.tone === "success" ? (
              <CircleCheck aria-hidden="true" className="text-success" />
            ) : shown.tone === "error" ? (
              <CircleAlert aria-hidden="true" className="text-destructive" />
            ) : null}
            <InputGroupButton
              size="icon-sm"
              className="hover:text-foreground"
              aria-label={`Show ${secretLabel.toLowerCase()}`}
              aria-pressed={form.shown}
              onClick={form.toggleShown}
            >
              {form.shown ? (
                <EyeOff aria-hidden="true" />
              ) : (
                <Eye aria-hidden="true" />
              )}
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
        <p
          id={ids.status}
          role="status"
          data-testid="credential-status"
          data-tone={shown.tone}
          className={cn("text-sm", STATUS_TEXT[shown.tone])}
        >
          {shown.text}
        </p>
        <FieldDescription id={ids.secretHint}>
          Saving replaces any credential saved before, then checks it with
          Anthropic.
        </FieldDescription>
        {secretError ? (
          <FieldError id={ids.secretError}>{secretError}</FieldError>
        ) : null}
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          type="submit"
          disabled={form.save.isPending || saveLocked}
          aria-busy={form.save.isPending}
        >
          {form.save.isPending ? (
            <>
              <Spinner aria-hidden="true" role={undefined} />
              Saving
            </>
          ) : saveLocked ? (
            <>
              <Timer aria-hidden="true" />
              <span className="tabular-nums">
                Try again in {formatWait(form.saveWait.remaining)}
              </span>
            </>
          ) : saved ? (
            "Replace credential"
          ) : (
            "Save credential"
          )}
        </Button>
        {saved ? (
          <Button type="button" variant="outline" onClick={onRemove}>
            Remove
          </Button>
        ) : null}
      </div>
    </form>
  );
}
