import { CircleAlert, Eye, EyeOff, Timer } from "lucide-react";
import { useId } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CardContent, CardFooter } from "@/components/ui/card";
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
import { CredentialKindOption } from "./credential-kind-option";

/** The form of the Model access card: which kind of credential, the secret, and Save. */
export function CredentialForm({
  saved,
  onSaving,
  onSaved,
}: {
  saved: ModelCredential | null;
  onSaving: () => void;
  onSaved: (value: ModelCredential) => void;
}) {
  const form = useCredentialForm({ saved, onSaving, onSaved });
  const ids = { secretHint: useId(), secretError: useId() };
  const { chosen, secretError } = form;
  const secretLabel =
    chosen === "claude_subscription" ? "Subscription token" : "API key";
  const saveLocked = form.saveWait.remaining > 0;

  return (
    <form
      noValidate
      aria-label="Model access"
      onSubmit={form.submit}
      className="flex flex-col gap-6"
    >
      <CardContent className="flex flex-col gap-5">
        {form.formError ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{form.formError}</AlertDescription>
          </Alert>
        ) : null}
        <FieldSet className="gap-3">
          <FieldLegend variant="label">
            {saved ? "Replace with" : "Credential type"}
          </FieldLegend>
          <RadioGroup
            name="kind"
            value={chosen}
            onValueChange={(value) => {
              if (isKind(value)) form.choose(value);
            }}
          >
            <CredentialKindOption
              kind="anthropic_api_key"
              checked={chosen === "anthropic_api_key"}
            >
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
            <CredentialKindOption
              kind="claude_subscription"
              checked={chosen === "claude_subscription"}
            >
              For the account owner's personal use only: a Claude subscription
              cannot be shared with other people. In a terminal with Claude Code
              installed, run{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">
                claude setup-token
              </code>
              , sign in, and paste the token it prints.
            </CredentialKindOption>
          </RadioGroup>
        </FieldSet>

        <Field className="gap-2" data-invalid={secretError ? true : undefined}>
          <FieldLabel htmlFor={form.secretId}>{secretLabel}</FieldLabel>
          <InputGroup>
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
              aria-invalid={secretError ? true : undefined}
              aria-describedby={
                secretError
                  ? `${ids.secretHint} ${ids.secretError}`
                  : ids.secretHint
              }
              onChange={(event) => {
                form.changeSecret(event.target.value);
              }}
            />
            <InputGroupAddon align="inline-end">
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
          <FieldDescription id={ids.secretHint}>
            Saving replaces any credential saved before. It stays unverified
            until you click Verify.
          </FieldDescription>
          {secretError ? (
            <FieldError id={ids.secretError}>{secretError}</FieldError>
          ) : null}
        </Field>
      </CardContent>
      <CardFooter className="justify-end">
        <Button
          type="submit"
          className="w-full sm:w-auto"
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
      </CardFooter>
    </form>
  );
}
