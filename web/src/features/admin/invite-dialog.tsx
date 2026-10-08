import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CircleAlert,
  Copy,
  LoaderCircle,
  TriangleAlert,
  UserPlus,
} from "lucide-react";
import { useId, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { SERVER_UNREACHABLE } from "@/features/auth/login-errors";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import {
  createInvite,
  fullLink,
  invitesKey,
  LIFETIME,
  type InviteCreated,
  type NewInvite,
} from "./api";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const schema = z.object({
  email: z
    .string()
    .trim()
    .min(1, "Enter an email address.")
    .max(320, "Use at most 320 characters for the email.")
    .regex(EMAIL_PATTERN, "Enter a valid email address."),
  kind: z.enum(["invite", "reset"]),
  role: z.enum(["member", "admin"]),
});
type Values = z.infer<typeof schema>;

const DEFAULTS: Values = { email: "", kind: "invite", role: "member" };

type CopyState = "idle" | "copied" | "failed";

/**
 * "Invite someone": an email, a kind (invite or password reset) and, for an invite, a role.
 * On success the dialog shows the full link once, with a Copy button; closing it forgets the
 * link for good (the api never returns it again).
 */
export function InviteDialog() {
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<InviteCreated>();

  const onOpenChange = (next: boolean) => {
    setOpen(next);
    // The link lives only while this dialog shows it.
    if (!next) setCreated(undefined);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button>
          <UserPlus aria-hidden="true" />
          Invite someone
        </Button>
      </DialogTrigger>
      <DialogContent
        // With a link on screen, a stray click outside must not throw it away.
        onInteractOutside={(event) => {
          if (created) event.preventDefault();
        }}
      >
        {created ? (
          <LinkResult
            created={created}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : (
          <InviteForm onCreated={setCreated} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function InviteForm({
  onCreated,
}: {
  onCreated: (created: InviteCreated) => void;
}) {
  const client = useQueryClient();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: DEFAULTS,
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const [formError, setFormError] = useState<string>();

  const mutation = useMutation({
    mutationFn: (values: Values) => {
      const body: NewInvite =
        values.kind === "invite"
          ? { email: values.email, kind: "invite", role: values.role }
          : { email: values.email, kind: "reset" };
      return createInvite(body);
    },
    onMutate: () => {
      setFormError(undefined);
    },
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: invitesKey });
      onCreated(created);
    },
    onError: (error) => {
      const detail = errorDetail(error);
      // 409 account exists, 404 no active account for a reset, 422 invalid email: all about
      // the address, so the api's sentence goes under the email field.
      if (
        isApiError(error) &&
        [404, 409, 422].includes(error.status ?? 0) &&
        detail
      ) {
        form.setError("email", { message: detail }, { shouldFocus: true });
        return;
      }
      setFormError(
        isApiError(error) &&
          (error.kind === "network" || error.kind === "timeout")
          ? SERVER_UNREACHABLE
          : (detail ?? describeError(error)),
      );
    },
  });

  return (
    <>
      <DialogHeader>
        <DialogTitle>Invite someone</DialogTitle>
        <DialogDescription>
          Create a one-time link and send it yourself, by message or any way you
          like. Nothing is emailed.
        </DialogDescription>
      </DialogHeader>
      <Form {...form}>
        <form
          noValidate
          aria-label="Invite someone"
          className="flex flex-col gap-4"
          onSubmit={(event) =>
            void form.handleSubmit((values) => {
              mutation.mutate(values);
            })(event)
          }
        >
          {formError ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Email</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    autoComplete="off"
                    inputMode="email"
                    spellCheck={false}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="kind"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Link</FormLabel>
                <FormControl>
                  <NativeSelect {...field}>
                    <option value="invite">Invite a new person</option>
                    <option value="reset">Password reset</option>
                  </NativeSelect>
                </FormControl>
                <FormDescription>
                  {kind === "invite"
                    ? `Creates an account. The link works once, for ${LIFETIME.invite}.`
                    : `For someone who has an account and forgot the password. The link works once, for ${LIFETIME.reset}.`}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          {kind === "invite" ? (
            <FormField
              control={form.control}
              name="role"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Role</FormLabel>
                  <FormControl>
                    <NativeSelect {...field}>
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
                    </NativeSelect>
                  </FormControl>
                  <FormDescription>
                    Admins can invite people and manage accounts.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : null}
          <DialogFooter>
            <Button
              type="submit"
              disabled={mutation.isPending}
              aria-busy={mutation.isPending}
            >
              {mutation.isPending ? (
                <>
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                  Creating link
                </>
              ) : (
                "Create link"
              )}
            </Button>
          </DialogFooter>
        </form>
      </Form>
    </>
  );
}

function LinkResult({
  created,
  onDone,
}: {
  created: InviteCreated;
  onDone: () => void;
}) {
  const link = fullLink(created.path);
  const { kind, email } = created.invite;
  const input = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [copy, setCopy] = useState<CopyState>("idle");

  const selectLink = () => {
    input.current?.focus();
    input.current?.select();
  };

  const onCopy = async () => {
    try {
      // Missing outside a secure context (plain http on another host than localhost).
      const clipboard = navigator.clipboard as Clipboard | undefined;
      if (!clipboard) throw new Error("no clipboard");
      await clipboard.writeText(link);
      setCopy("copied");
      toast.success("Link copied.");
    } catch {
      setCopy("failed");
      selectLink();
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {kind === "invite" ? "Invite link ready" : "Reset link ready"}
        </DialogTitle>
        <DialogDescription>
          Send this link to {email} yourself.
        </DialogDescription>
      </DialogHeader>
      <Alert className="border-warning/40 bg-warning-muted text-foreground">
        <TriangleAlert aria-hidden="true" className="text-warning" />
        <AlertTitle>Copy it now: it is shown only once</AlertTitle>
        <AlertDescription className="text-foreground/80">
          You cannot see this link again after closing. It works once and
          expires in {LIFETIME[kind]}. If it gets lost, create a new one: the
          old link stops working.
        </AlertDescription>
      </Alert>
      <div className="grid gap-2">
        <label htmlFor={inputId} className="text-sm font-medium">
          {kind === "invite" ? "Invite link" : "Reset link"}
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            id={inputId}
            ref={input}
            readOnly
            value={link}
            onFocus={(event) => {
              event.currentTarget.select();
            }}
            className="font-mono text-xs md:text-xs"
          />
          <Button
            type="button"
            variant={copy === "copied" ? "secondary" : "default"}
            onClick={() => void onCopy()}
          >
            {copy === "copied" ? (
              <Check aria-hidden="true" />
            ) : (
              <Copy aria-hidden="true" />
            )}
            {copy === "copied" ? "Copied" : "Copy link"}
          </Button>
        </div>
        <p
          role="status"
          aria-live="polite"
          className={
            copy === "failed"
              ? "text-sm text-destructive"
              : "text-sm text-muted-foreground"
          }
        >
          {copy === "failed"
            ? "This browser blocked copying. The link is selected: press Ctrl+C, or Cmd+C on a Mac."
            : copy === "copied"
              ? "The link is on your clipboard."
              : ""}
        </p>
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Done
        </Button>
      </DialogFooter>
    </>
  );
}
