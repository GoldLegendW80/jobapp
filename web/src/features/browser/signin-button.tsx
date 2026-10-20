import { useMutation } from "@tanstack/react-query";
import { LoaderCircle, LogIn } from "lucide-react";
import { useId, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { describeError, errorDetail } from "@/lib/api";

import { startSession, type Session } from "./browser-api";

// The api's own cap on the typed address (api/app/browser/address.py, ADDRESS_MAX).
export const ADDRESS_MAX = 2048;

/**
 * "Sign in to a site" (SPEC-browser.md, Persistent profiles and logins): the person types the
 * address of a site and their browser opens there with no assistant connected, the person holding
 * the keyboard and the mouse in the live view, so they sign in themselves and the profile keeps
 * the login. The api checks the address (422 `bad_address`, whose sentence is shown under the
 * field, and nothing starts). `onStarted` gets the session, or the place in the queue, and the
 * address, so the card can keep that place by asking again.
 */
export function SignInButton({
  disabled,
  onStarted,
}: {
  disabled: boolean;
  onStarted: (session: Session, address: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [address, setAddress] = useState("");
  const field = useRef<HTMLInputElement>(null);
  const fieldId = useId();
  const hintId = useId();
  const errorId = useId();
  const start = useMutation({
    mutationFn: (typed: string) => startSession("signin", typed),
    onSuccess: (session, typed) => {
      setOpen(false);
      setAddress("");
      onStarted(session, typed);
    },
    onError: () => {
      field.current?.focus();
    },
  });
  const failure = start.error;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    start.mutate(address);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (start.isPending) return;
        if (!next) {
          start.reset();
          setAddress("");
        }
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="outline" disabled={disabled}>
          <LogIn aria-hidden />
          Sign in to a site
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form noValidate onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Sign in to a site</DialogTitle>
            <DialogDescription>
              Your browser opens at this address with no assistant connected.
              You sign in yourself, and your browser keeps that login for next
              time. The app saves no password.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor={fieldId}>Address of the site</Label>
            <Input
              ref={field}
              id={fieldId}
              type="url"
              inputMode="url"
              autoComplete="url"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={ADDRESS_MAX}
              placeholder="https://example.org/login"
              value={address}
              onChange={(event) => {
                setAddress(event.target.value);
              }}
              aria-invalid={failure ? true : undefined}
              aria-describedby={failure ? `${hintId} ${errorId}` : hintId}
            />
            <p id={hintId} className="text-sm text-muted-foreground">
              The whole address, starting with https:// or http://. A site name
              in another alphabet is typed in its xn-- form.
            </p>
            {failure ? (
              <p id={errorId} role="alert" className="text-sm text-destructive">
                {errorDetail(failure) ?? describeError(failure)}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={start.isPending}
              onClick={() => {
                start.reset();
                setAddress("");
                setOpen(false);
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={start.isPending}
              aria-busy={start.isPending}
            >
              {start.isPending ? (
                <LoaderCircle aria-hidden className="animate-spin" />
              ) : null}
              Open the site
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
