import {
  CircleAlert,
  CircleCheck,
  Eye,
  EyeOff,
  Info,
  KeyRound,
} from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { fieldError } from "@/features/account/helpers/field-error";
import { message } from "@/features/admin/helpers/errors";
import {
  useModelKey,
  useSaveModelKey,
  useVerifyModelKey,
} from "@/features/agents/api/use-model-key";
import { WORDING } from "@/lib/wording";

const EMPTY = "Paste the key, or leave it for later.";

type Outcome = { tone: "success" | "info" | "error"; text: string };

/**
 * The summary's optional model key (owner, audit chapter 2: "at last possible step"). While no
 * credential is saved, a form to paste an Anthropic API key, saved through Model access's own
 * route (PUT /api/me/model-credential, SPEC-agents "Credentials"), as the Account card saves it:
 * the field is masked, the key is never shown back (the field is emptied and the mutation that
 * held it dropped on success), and only the last four characters the api answers are shown,
 * with Verify. Saving is optional: Start never waits for it. Says nothing while the credential
 * loads or cannot be read, nor when one was saved before this page opened.
 */
export function ModelKeyForm() {
  const credential = useModelKey();
  const ids = {
    title: useId(),
    secret: useId(),
    hint: useId(),
    error: useId(),
  };
  const [secret, setSecret] = useState("");
  const [shown, setShown] = useState(false);
  const [secretError, setSecretError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [savedHere, setSavedHere] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>();

  const save = useSaveModelKey({
    onStart: () => {
      setSecretError(undefined);
      setFormError(undefined);
    },
    onSaved: () => {
      setSecret("");
      setShown(false);
      setSavedHere(true);
      save.reset();
    },
    onError: (error) => {
      const found = fieldError(error, ["secret"] as const);
      if (found) {
        setSecretError(found.message);
        document.getElementById(ids.secret)?.focus();
        return;
      }
      setFormError(message(error));
    },
  });

  const verify = useVerifyModelKey({
    onStart: () => {
      setOutcome(undefined);
    },
    onVerified: (result) => {
      setOutcome({
        tone: result.result === "verified" ? "success" : "info",
        text: result.message,
      });
    },
    onError: (error) => {
      setOutcome({ tone: "error", text: message(error) });
    },
  });

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (secret.trim() === "") {
      setSecretError(EMPTY);
      document.getElementById(ids.secret)?.focus();
      return;
    }
    save.mutate({ kind: "anthropic_api_key", secret });
  };

  const saved = credential.data;
  if (savedHere && saved)
    return (
      <section
        aria-labelledby={ids.title}
        className="flex flex-col gap-3 border-t pt-5"
        data-simple-card
      >
        <h3 id={ids.title} className="text-sm font-medium">
          {WORDING.yourApiKeyOptional}
        </h3>
        <p
          role="status"
          className="flex items-start gap-2 text-sm"
          data-testid="welcome-key-saved"
        >
          <KeyRound aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>
            Your key is saved. It ends with{" "}
            <span className="font-mono">{saved.last4}</span>.{" "}
            {saved.verified_at
              ? "It is verified."
              : "Verify asks Anthropic whether it works, and spends no tokens."}
          </span>
        </p>
        {saved.verified_at ? null : (
          <Button
            type="button"
            variant="outline"
            className="w-full sm:w-fit"
            disabled={verify.isPending}
            aria-busy={verify.isPending}
            onClick={() => {
              verify.mutate();
            }}
          >
            {verify.isPending ? <Spinner aria-hidden="true" /> : null}
            {WORDING.verify}
          </Button>
        )}
        {outcome ? (
          <Alert
            variant={outcome.tone === "error" ? "destructive" : "default"}
            aria-live="polite"
          >
            {outcome.tone === "success" ? (
              <CircleCheck aria-hidden="true" />
            ) : outcome.tone === "info" ? (
              <Info aria-hidden="true" />
            ) : (
              <CircleAlert aria-hidden="true" />
            )}
            <AlertDescription>{outcome.text}</AlertDescription>
          </Alert>
        ) : null}
      </section>
    );
  // Loading, unreadable, or saved before: nothing to ask.
  if (saved !== null) return null;
  return (
    <section
      aria-labelledby={ids.title}
      className="flex flex-col gap-3 border-t pt-5"
      data-simple-card
    >
      <h3 id={ids.title} className="text-sm font-medium">
        {WORDING.yourApiKeyOptional}
      </h3>
      <p className="text-sm text-muted-foreground">
        Reading your CV, searching websites, writing your CVs and letters and
        applying use your own Anthropic API key, billed to your Anthropic
        account. Create one in the{" "}
        <a
          href="https://console.anthropic.com/settings/keys"
          target="_blank"
          rel="noreferrer noopener"
          className="font-medium text-foreground underline underline-offset-4"
        >
          {WORDING.anthropicConsole}
        </a>{" "}
        and paste it here, or later in Account. Start works without it.
      </p>
      <form
        noValidate
        aria-labelledby={ids.title}
        onSubmit={onSubmit}
        className="flex flex-col gap-3"
      >
        {formError ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}
        <Field className="gap-1.5">
          <FieldLabel htmlFor={ids.secret} className="leading-none">
            {WORDING.apiKey}
          </FieldLabel>
          <InputGroup>
            <InputGroupInput
              id={ids.secret}
              name="secret"
              type={shown ? "text" : "password"}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              className="font-mono"
              placeholder="sk-ant-api03-…"
              value={secret}
              maxLength={4096}
              aria-invalid={secretError ? true : undefined}
              aria-describedby={
                secretError ? `${ids.hint} ${ids.error}` : ids.hint
              }
              onChange={(event) => {
                setSecret(event.target.value);
                setSecretError(undefined);
              }}
            />
            <InputGroupAddon align="inline-end">
              <InputGroupButton
                size="icon-sm"
                className="hover:text-foreground"
                aria-label={WORDING.showApiKey}
                aria-pressed={shown}
                onClick={() => {
                  setShown((value) => !value);
                }}
              >
                {shown ? (
                  <EyeOff aria-hidden="true" />
                ) : (
                  <Eye aria-hidden="true" />
                )}
              </InputGroupButton>
            </InputGroupAddon>
          </InputGroup>
          <FieldDescription id={ids.hint} className="text-xs nth-last-2:mt-0">
            It is stored encrypted and never shown again: only its last four
            characters are.
          </FieldDescription>
          <FieldError id={ids.error}>{secretError}</FieldError>
        </Field>
        <Button
          type="submit"
          variant="outline"
          className="w-full sm:w-fit"
          disabled={save.isPending}
          aria-busy={save.isPending}
        >
          {save.isPending ? <Spinner aria-hidden="true" /> : null}
          {WORDING.saveKey}
        </Button>
      </form>
    </section>
  );
}
