import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { z } from "zod";

import { LoadError } from "@/components/blocks/load-error";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldLabel } from "@/components/ui/field";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";

import type { Invite } from "../api/auth-api";
import { useAcceptInvite, useInviteLink } from "../api/use-invite-link";
import { useAuth } from "../helpers/auth-context";
import { AuthLayout } from "@/components/shared/auth/auth-layout";
import { PasswordField } from "@/components/shared/auth/password-field";
import { InvalidLink } from "../components/invalid-link";
import { SERVER_UNREACHABLE } from "../helpers/login-errors";
import {
  cleanName,
  nameProblem,
  PASSWORD_HINT,
  passwordProblem,
} from "../helpers/password-rules";

type Mode = "invite" | "reset";

const COPY = {
  invite: {
    title: "Set up your account",
    description: "You are invited to jobapp. Choose your name and a password.",
    submit: "Create account",
    pending: "Creating account",
  },
  reset: {
    title: "Choose a new password",
    description: "Set a new password for your account.",
    submit: "Save password",
    pending: "Saving",
  },
} as const;

/** The api's token shape (43 URL-safe characters); anything else is dead without asking. */
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const is404 = (error: unknown) => isApiError(error) && error.status === 404;

/**
 * /invite/:token and /reset/:token. The api decides what the link is: an invite asks for a
 * name, a reset does not, whichever path it was opened on. Every dead link shows the same page.
 */
export function LinkPage({ mode }: { mode: Mode }) {
  const { token = "" } = useParams();
  const wellFormed = TOKEN_PATTERN.test(token);
  const check = useInviteLink(token, wellFormed);
  const [dead, setDead] = useState(false);
  const pageTitle = COPY[check.data?.kind ?? mode].title;
  useTitle(wellFormed ? pageTitle : "Invalid link");

  if (!wellFormed || dead || (check.isError && is404(check.error)))
    return <InvalidLink />;
  if (check.isPending)
    return (
      <AuthLayout>
        <Card aria-busy="true">
          <CardHeader>
            <CardTitle>
              <h1 className="text-xl">{COPY[mode].title}</h1>
            </CardTitle>
            <CardDescription role="status" aria-live="polite">
              <span className="delayed-appear inline-flex items-center gap-2">
                <Spinner aria-hidden="true" role={undefined} />
                Checking your link
              </span>
            </CardDescription>
          </CardHeader>
        </Card>
      </AuthLayout>
    );
  if (check.isError)
    return (
      <AuthLayout>
        <Card>
          <CardHeader>
            <CardTitle>
              <h1 className="text-xl">{COPY[mode].title}</h1>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <LoadError query={check}>Your link could not be checked.</LoadError>
          </CardContent>
        </Card>
      </AuthLayout>
    );
  return (
    <SetPasswordForm
      token={token}
      invite={check.data}
      onDead={() => {
        setDead(true);
      }}
    />
  );
}

function buildSchema(invite: Invite) {
  return z
    .object({ name: z.string(), password: z.string(), confirm: z.string() })
    .superRefine((values, ctx) => {
      if (invite.kind === "invite") {
        const problem = nameProblem(values.name);
        if (problem)
          ctx.addIssue({ code: "custom", path: ["name"], message: problem });
      }
      const problem = passwordProblem(values.password, invite.email);
      if (problem)
        ctx.addIssue({ code: "custom", path: ["password"], message: problem });
      if (values.confirm === "")
        ctx.addIssue({
          code: "custom",
          path: ["confirm"],
          message: "Repeat the password.",
        });
      else if (values.confirm !== values.password)
        ctx.addIssue({
          code: "custom",
          path: ["confirm"],
          message: "The passwords do not match.",
        });
    });
}
type Values = z.infer<ReturnType<typeof buildSchema>>;

function SetPasswordForm({
  token,
  invite,
  onDead,
}: {
  token: string;
  invite: Invite;
  onDead: () => void;
}) {
  const copy = COPY[invite.kind];
  const navigate = useNavigate();
  const { signIn } = useAuth();
  const schema = useMemo(() => buildSchema(invite), [invite]);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", password: "", confirm: "" },
  });
  const [formError, setFormError] = useState<string>();

  const mutation = useAcceptInvite(token, {
    onMutate: () => {
      setFormError(undefined);
    },
    onSuccess: (user) => {
      signIn(user);
      toast.success(
        invite.kind === "invite"
          ? `Welcome, ${user.name}.`
          : "Your new password is saved.",
      );
      void navigate("/jobs", { replace: true });
    },
    onError: (error) => {
      if (is404(error)) {
        onDead();
        return;
      }
      // The api's rule message, word for word, next to the field it is about.
      const detail = errorDetail(error);
      if (isApiError(error) && error.status === 422 && detail) {
        if (/password/i.test(detail)) {
          form.setError("password", { message: detail }, { shouldFocus: true });
          return;
        }
        if (invite.kind === "invite" && /\bname\b/i.test(detail)) {
          form.setError("name", { message: detail }, { shouldFocus: true });
          return;
        }
      }
      setFormError(
        isApiError(error) &&
          (error.kind === "network" || error.kind === "timeout")
          ? SERVER_UNREACHABLE
          : (detail ?? describeError(error)),
      );
    },
  });
  const pending = mutation.isPending || mutation.isSuccess;

  return (
    <AuthLayout>
      <Card>
        <CardHeader>
          <CardTitle>
            <h1 className="text-xl">{copy.title}</h1>
          </CardTitle>
          <CardDescription>{copy.description}</CardDescription>
        </CardHeader>
        <Form {...form}>
          <form
            noValidate
            aria-label={copy.title}
            onSubmit={(event) =>
              void form.handleSubmit((values) => {
                mutation.mutate(
                  invite.kind === "invite"
                    ? {
                        name: cleanName(values.name),
                        password: values.password,
                      }
                    : { password: values.password },
                );
              })(event)
            }
          >
            <CardContent className="flex flex-col gap-4">
              {formError ? (
                <Alert variant="destructive">
                  <CircleAlert aria-hidden="true" />
                  <AlertDescription>{formError}</AlertDescription>
                </Alert>
              ) : null}
              <Field className="gap-2">
                <FieldLabel htmlFor="link-email">Email</FieldLabel>
                <Input
                  id="link-email"
                  type="email"
                  value={invite.email}
                  readOnly
                  aria-readonly="true"
                  autoComplete="username"
                  className="bg-muted text-muted-foreground"
                />
              </Field>
              {invite.kind === "invite" ? (
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name</FormLabel>
                      <FormControl>
                        <Input autoComplete="name" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ) : null}
              <PasswordField
                control={form.control}
                name="password"
                label={invite.kind === "invite" ? "Password" : "New password"}
                autoComplete="new-password"
                description={PASSWORD_HINT}
              />
              <PasswordField
                control={form.control}
                name="confirm"
                label="Confirm password"
                autoComplete="new-password"
              />
            </CardContent>
            <CardFooter className="mt-6">
              <Button
                type="submit"
                className="w-full"
                disabled={pending}
                aria-busy={pending}
              >
                {pending ? (
                  <>
                    <Spinner aria-hidden="true" role={undefined} />
                    {copy.pending}
                  </>
                ) : (
                  copy.submit
                )}
              </Button>
            </CardFooter>
          </form>
        </Form>
      </Card>
    </AuthLayout>
  );
}
