import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { describeError, errorDetail } from "@/lib/api";

import {
  browserQueryKey,
  forgetLogins,
  sizeText,
  type Browser,
} from "./browser-api";

export const FORGET_LOST =
  "Your browser on the server forgets every site you signed in to: their cookies, saved site data, history and open tabs are deleted.";
export const FORGET_ENDS =
  "If your browser is open now, it closes, even when the assistant is using it.";
export const FORGET_NEXT =
  "Next time it starts empty and each site asks you to sign in again. Your accounts on those sites are not deleted.";

/**
 * "Forget all logins" (SPEC-browser.md, Frontend), behind a confirmation that names what is lost:
 * the person's whole browser profile, the session it ends, and what stays (the accounts on the
 * sites). The api ends any lease of the person, the assistant's included, and deletes the profile,
 * so the next lease starts empty; a refusal is shown in the dialog and nothing is deleted.
 */
export function ForgetDialog({
  profile,
  onForgotten,
}: {
  profile: Browser["profile"] | undefined;
  onForgotten: () => void;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const forget = useMutation({
    mutationFn: forgetLogins,
    onSuccess: () => {
      setOpen(false);
      onForgotten();
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: browserQueryKey });
    },
  });
  const close = () => {
    if (forget.isPending) return;
    forget.reset();
    setOpen(false);
  };
  const held = profile?.exists && profile.size_bytes > 0;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        onClick={() => {
          setOpen(true);
        }}
      >
        <Trash2 aria-hidden />
        Forget all logins
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (!next) close();
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Forget all logins?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="flex flex-col gap-2 text-sm text-muted-foreground">
                <p>
                  {FORGET_LOST}
                  {held ? ` It holds ${sizeText(profile.size_bytes)}.` : null}
                </p>
                <p>{FORGET_ENDS}</p>
                <p>{FORGET_NEXT}</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          {forget.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {errorDetail(forget.error) ?? describeError(forget.error)}
            </p>
          ) : null}
          <AlertDialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={forget.isPending}
              onClick={close}
            >
              Keep my logins
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={forget.isPending}
              aria-busy={forget.isPending}
              onClick={() => {
                forget.mutate();
              }}
            >
              {forget.isPending ? (
                <LoaderCircle aria-hidden className="animate-spin" />
              ) : null}
              Forget all logins
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
