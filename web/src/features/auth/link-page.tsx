import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle, RotateCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { z } from "zod";

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
import { Label } from "@/components/ui/label";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { acceptInvite, checkInvite, type Invite } from "./api";
import { useAuth } from "./auth-context";
import { AuthLayout } from "./auth-layout";
import { InvalidLink } from "./invalid-link";
import { SERVER_UNREACHABLE } from "./login-errors";
import {
  cleanName,
  nameProblem,
  PASSWORD_HINT,
  passwordProblem,
} from "./password-rules";

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

const linkQueryKey = (token: string) => ["link", token] as const;
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
  const check = useQuery({
    queryKey: linkQueryKey(token),
    queryFn: ({ signal }) => checkInvite(token, signal),
    enabled: wellFormed,
    staleTime: Infinity,
    gcTime: 0,
  });
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
                <LoaderCircle
                  aria-hidden="true"
                  className="size-4 animate-spin"
                />
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
          <CardContent className="flex flex-col gap-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                {isApiError(check.error) &&
                (check.error.kind === "network" ||
                  check.error.kind === "timeout")
                  ? SERVER_UNREACHABLE
                  : describeError(check.error)}
              </AlertDescription>
            </Alert>
            <Button
              variant="outline"
              onClick={() => void check.refetch()}
              disabled={check.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
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
  const client = useQueryClient();
  const navigate = useNavigate();
  const { signIn } = useAuth();
  const schema = useMemo(() => buildSchema(invite), [invite]);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", password: "", confirm: "" },
  });
  const [formError, setFormError] = useState<string>();

  const mutation = useMutation({
    mutationFn: (values: Values) =>
      acceptInvite(
        token,
        invite.kind === "invite"
          ? { name: cleanName(values.name), password: values.password }
          : { password: values.password },
      ),
    onMutate: () => {
      setFormError(undefined);
    },
    onSuccess: (user) => {
      signIn(user);
      client.removeQueries({ queryKey: ["link"] });
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
                mutation.mutate(values);
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
              <div className="grid gap-2">
                <Label htmlFor="link-email">Email</Label>
                <Input
                  id="link-email"
                  type="email"
                  value={invite.email}
                  readOnly
                  aria-readonly="true"
                  autoComplete="username"
                  className="bg-muted text-muted-foreground"
                />
              </div>
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
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {invite.kind === "invite" ? "Password" : "New password"}
                    </FormLabel>
                    <FormControl>
                      <Input
                        type="password"
                        autoComplete="new-password"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>{PASSWORD_HINT}</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="confirm"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Confirm password</FormLabel>
                    <FormControl>
                      <Input
                        type="password"
                        autoComplete="new-password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
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
                    <LoaderCircle aria-hidden="true" className="animate-spin" />
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
