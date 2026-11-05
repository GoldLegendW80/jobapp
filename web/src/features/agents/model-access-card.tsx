import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  CircleAlert,
  CircleCheck,
  Eye,
  EyeOff,
  Info,
  KeyRound,
  RotateCw,
  Timer,
} from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";
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
  Field,
  FieldContent,
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";
import { fieldError } from "@/features/account/field-error";
import { exactTime } from "@/features/admin/relative-time";
import { message } from "@/features/admin/errors";
import {
  formatWait,
  throttledFor,
  useRetryCountdown,
} from "@/features/auth/retry-countdown";
import { cn } from "@/lib/utils";

import {
  getModelCredential,
  modelCredentialQueryKey,
  removeModelCredential,
  saveModelCredential,
  verifyModelCredential,
  type CredentialKind,
  type ModelCredential,
} from "./api";

const KIND_LABEL: Record<CredentialKind, string> = {
  claude_subscription: "Claude subscription token",
  anthropic_api_key: "Anthropic API key",
};
export const SAVED = "Credential saved. Verify it to check it works.";
export const REMOVED = "Credential removed.";
const EMPTY = "Paste the token or key.";

type Outcome =
  { tone: "success" | "info"; text: string } | { tone: "error"; text: string };

/** Account page, "Model access": the Claude credential the user's agents run on. */
export function ModelAccessCard() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
  });
  const saved = query.data ?? null;
  const setSaved = (value: ModelCredential | null) => {
    client.setQueryData(modelCredentialQueryKey, value);
  };

  const ids = {
    title: useId(),
    secret: useId(),
    secretHint: useId(),
    secretError: useId(),
  };
  const [kind, setKind] = useState<CredentialKind | undefined>();
  const chosen: CredentialKind = kind ?? saved?.kind ?? "anthropic_api_key";
  const [secret, setSecret] = useState("");
  const [shown, setShown] = useState(false);
  const [secretError, setSecretError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [outcome, setOutcome] = useState<Outcome>();
  const [confirming, setConfirming] = useState(false);
  const saveWait = useRetryCountdown(() => {
    setFormError(undefined);
  });
  const verifyWait = useRetryCountdown();

  const save = useMutation({
    mutationFn: saveModelCredential,
    // The variables hold the secret: once the mutation has no observer it leaves the cache at
    // once instead of after the default 5 minutes.
    gcTime: 0,
    onMutate: () => {
      setFormError(undefined);
      setSecretError(undefined);
      setOutcome(undefined);
    },
    onSuccess: (value) => {
      setSaved(value);
      setSecret("");
      setShown(false);
      setKind(undefined);
      toast.success(SAVED);
      // Drop the mutation (its variables are the secret) instead of keeping it until the next
      // save; with gcTime 0 the cache forgets it too.
      save.reset();
    },
    onError: (error) => {
      const wait = throttledFor(error);
      if (wait !== undefined) saveWait.start(wait);
      const found = fieldError(error, ["secret"] as const);
      if (found) {
        setSecretError(found.message);
        document.getElementById(ids.secret)?.focus();
        return;
      }
      setFormError(message(error));
    },
  });

  const verify = useMutation({
    mutationFn: verifyModelCredential,
    onMutate: () => {
      setOutcome(undefined);
    },
    onSuccess: (result) => {
      setSaved(result.credential);
      setOutcome({
        tone: result.result === "verified" ? "success" : "info",
        text: result.message,
      });
    },
    onError: (error) => {
      const wait = throttledFor(error);
      if (wait !== undefined) verifyWait.start(wait);
      setOutcome({ tone: "error", text: message(error) });
      // A refused key is no longer verified; the server cleared it.
      void client.invalidateQueries({ queryKey: modelCredentialQueryKey });
    },
  });

  const remove = useMutation({
    mutationFn: removeModelCredential,
    onSuccess: () => {
      setSaved(null);
      setOutcome(undefined);
      setConfirming(false);
      toast.success(REMOVED);
    },
  });

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (secret.trim() === "") {
      setSecretError(EMPTY);
      document.getElementById(ids.secret)?.focus();
      return;
    }
    save.mutate({ kind: chosen, secret });
  };

  const secretLabel =
    chosen === "claude_subscription" ? "Subscription token" : "API key";
  const saveLocked = saveWait.remaining > 0;
  const verifyLocked = verifyWait.remaining > 0;

  return (
    <Card aria-labelledby={ids.title}>
      <CardHeader>
        <CardTitle>
          <h2 id={ids.title} className="text-lg">
            Model access
          </h2>
        </CardTitle>
        <CardDescription>
          The Claude credential your agents run on. It is stored encrypted and
          never shown again: only its last four characters are.
        </CardDescription>
      </CardHeader>

      <CardContent className="flex flex-col gap-6">
        <SavedCredential
          query={query}
          saved={saved}
          verifying={verify.isPending}
          verifyLocked={verifyLocked}
          verifyWait={verifyWait.remaining}
          onVerify={() => {
            verify.mutate();
          }}
          onRemove={() => {
            remove.reset();
            setConfirming(true);
          }}
        />
        {outcome ? (
          <Alert
            variant={outcome.tone === "error" ? "destructive" : "default"}
            aria-live="polite"
            data-testid="verify-result"
          >
            {outcome.tone === "success" ? (
              <CircleCheck
                aria-hidden="true"
                className="text-emerald-700 dark:text-emerald-400"
              />
            ) : outcome.tone === "info" ? (
              <Info aria-hidden="true" />
            ) : (
              <CircleAlert aria-hidden="true" />
            )}
            <AlertDescription
              className={cn(outcome.tone !== "error" && "text-foreground")}
            >
              {outcome.text}
            </AlertDescription>
          </Alert>
        ) : null}
      </CardContent>

      <form
        noValidate
        aria-label="Model access"
        onSubmit={onSubmit}
        className="flex flex-col gap-6"
      >
        <CardContent className="flex flex-col gap-5">
          {formError ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{formError}</AlertDescription>
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
                if (isKind(value)) setKind(value);
              }}
            >
              <KindOption
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
              </KindOption>
              <KindOption
                kind="claude_subscription"
                checked={chosen === "claude_subscription"}
              >
                For the account owner's personal use only: a Claude subscription
                cannot be shared with other people. In a terminal with Claude
                Code installed, run{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">
                  claude setup-token
                </code>
                , sign in, and paste the token it prints.
              </KindOption>
            </RadioGroup>
          </FieldSet>

          <Field
            className="gap-2"
            data-invalid={secretError ? true : undefined}
          >
            <FieldLabel htmlFor={ids.secret}>{secretLabel}</FieldLabel>
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
                placeholder={
                  chosen === "claude_subscription"
                    ? "sk-ant-oat01-…"
                    : "sk-ant-api03-…"
                }
                value={secret}
                maxLength={4096}
                aria-invalid={secretError ? true : undefined}
                aria-describedby={
                  secretError
                    ? `${ids.secretHint} ${ids.secretError}`
                    : ids.secretHint
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
                  aria-label={`Show ${secretLabel.toLowerCase()}`}
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
            disabled={save.isPending || saveLocked}
            aria-busy={save.isPending}
          >
            {save.isPending ? (
              <>
                <Spinner aria-hidden="true" role={undefined} />
                Saving
              </>
            ) : saveLocked ? (
              <>
                <Timer aria-hidden="true" />
                <span className="tabular-nums">
                  Try again in {formatWait(saveWait.remaining)}
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

      <AlertDialog
        open={confirming}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setConfirming(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove the credential?</AlertDialogTitle>
            <AlertDialogDescription>
              Your agents stop running until you save a new one. The stored
              value is deleted from the server.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {remove.error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{message(remove.error)}</AlertDescription>
            </Alert>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              aria-busy={remove.isPending}
              onClick={() => {
                remove.mutate();
              }}
            >
              {remove.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Remove
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

const isKind = (value: string): value is CredentialKind => value in KIND_LABEL;

/** One kind of credential: the radio, its name and what it is, in a box. */
function KindOption({
  kind,
  checked,
  children,
}: {
  kind: CredentialKind;
  checked: boolean;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <Field
      orientation="horizontal"
      className={cn(
        "rounded-lg border p-3 transition-colors",
        checked ? "border-primary bg-accent/40" : "border-border",
      )}
    >
      <RadioGroupItem id={id} value={kind} aria-describedby={`${id}-hint`} />
      <FieldContent className="gap-1">
        <FieldLabel htmlFor={id} className="leading-none">
          {KIND_LABEL[kind]}
        </FieldLabel>
        <FieldDescription id={`${id}-hint`}>{children}</FieldDescription>
      </FieldContent>
    </Field>
  );
}

function SavedCredential({
  query,
  saved,
  verifying,
  verifyLocked,
  verifyWait,
  onVerify,
  onRemove,
}: {
  query: UseQueryResult<ModelCredential | null>;
  saved: ModelCredential | null;
  verifying: boolean;
  verifyLocked: boolean;
  verifyWait: number;
  onVerify: () => void;
  onRemove: () => void;
}) {
  if (query.isPending)
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Loading your credential
      </p>
    );
  if (query.isError)
    return (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertDescription>
          <p>{message(query.error)}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void query.refetch();
            }}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  if (!saved)
    return (
      <p className="text-sm text-muted-foreground" data-testid="no-credential">
        No credential saved. Agents need one to run.
      </p>
    );
  return (
    <section
      aria-label="Saved credential"
      className="flex flex-col gap-4 rounded-lg border bg-muted/30 p-4"
    >
      <div className="flex items-start gap-3">
        <KeyRound
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
        />
        <dl className="grid min-w-0 gap-1 text-sm">
          <div>
            <dt className="sr-only">Type</dt>
            <dd className="font-medium">{KIND_LABEL[saved.kind]}</dd>
          </div>
          <div className="flex flex-wrap gap-x-1">
            <dt className="text-muted-foreground">Ends with</dt>
            <dd className="font-mono" data-testid="credential-last4">
              {saved.last4}
            </dd>
          </div>
          <div className="flex flex-wrap gap-x-1">
            <dt className="text-muted-foreground">Status</dt>
            <dd data-testid="credential-status">
              {saved.verified_at ? (
                <>
                  Verified on{" "}
                  <time dateTime={saved.verified_at}>
                    {exactTime(saved.verified_at)}
                  </time>
                </>
              ) : (
                "Not verified"
              )}
            </dd>
          </div>
        </dl>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button
          variant="outline"
          className="w-full sm:w-auto"
          onClick={onRemove}
        >
          Remove
        </Button>
        <Button
          variant="secondary"
          className="w-full sm:w-auto"
          disabled={verifying || verifyLocked}
          aria-busy={verifying}
          onClick={onVerify}
        >
          {verifying ? (
            <>
              <Spinner aria-hidden="true" role={undefined} />
              Verifying
            </>
          ) : verifyLocked ? (
            <>
              <Timer aria-hidden="true" />
              <span className="tabular-nums">
                Try again in {formatWait(verifyWait)}
              </span>
            </>
          ) : (
            "Verify"
          )}
        </Button>
      </div>
    </section>
  );
}
