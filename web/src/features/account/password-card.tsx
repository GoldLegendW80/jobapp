import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
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
import { Form } from "@/components/ui/form";
import { message } from "@/features/admin/errors";
import { changePassword, type User } from "@/features/auth/api";
import { PASSWORD_HINT, passwordProblem } from "@/features/auth/password-rules";

import { fieldError } from "./field-error";
import { PasswordField } from "./password-field";

export const PASSWORD_CHANGED =
  "Password changed. Other devices are signed out.";
export const SAME_PASSWORD =
  "The new password must be different from the current one.";

function buildSchema(email: string) {
  return z
    .object({ current: z.string(), next: z.string(), confirm: z.string() })
    .superRefine((values, ctx) => {
      if (values.current === "")
        ctx.addIssue({
          code: "custom",
          path: ["current"],
          message: "Enter your current password.",
        });
      const problem = passwordProblem(values.next, email);
      if (problem)
        ctx.addIssue({ code: "custom", path: ["next"], message: problem });
      else if (
        values.current !== "" &&
        values.next.normalize("NFKC") === values.current.normalize("NFKC")
      )
        ctx.addIssue({
          code: "custom",
          path: ["next"],
          message: SAME_PASSWORD,
        });
      if (values.confirm === "")
        ctx.addIssue({
          code: "custom",
          path: ["confirm"],
          message: "Repeat the new password.",
        });
      else if (values.confirm !== values.next)
        ctx.addIssue({
          code: "custom",
          path: ["confirm"],
          message: "The passwords do not match.",
        });
    });
}
type Values = z.infer<ReturnType<typeof buildSchema>>;
const EMPTY: Values = { current: "", next: "", confirm: "" };
const FIELD = { current_password: "current", new_password: "next" } as const;

/** "Change password": the current one, then the new one twice. Other browsers sign out. */
export function PasswordCard({ user }: { user: User }) {
  const schema = useMemo(() => buildSchema(user.email), [user.email]);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: EMPTY,
  });
  const [formError, setFormError] = useState<string>();
  const mutation = useMutation({
    mutationFn: (values: Values) =>
      changePassword({
        current_password: values.current,
        new_password: values.next,
      }),
    onMutate: () => {
      setFormError(undefined);
    },
    onSuccess: () => {
      form.reset(EMPTY);
      toast.success(PASSWORD_CHANGED);
    },
    onError: (error) => {
      const found = fieldError(error, ["current_password", "new_password"]);
      if (found) {
        form.setError(
          FIELD[found.field],
          { message: found.message },
          { shouldFocus: true },
        );
        return;
      }
      setFormError(message(error));
    },
  });
  const pending = mutation.isPending;

  return (
    <Card aria-labelledby="password-card-title">
      <CardHeader>
        <CardTitle>
          <h2 id="password-card-title" className="text-lg">
            Change password
          </h2>
        </CardTitle>
        <CardDescription>
          Every other browser signed in to your account is signed out. This one
          stays signed in.
        </CardDescription>
      </CardHeader>
      <Form {...form}>
        <form
          noValidate
          aria-label="Change password"
          onSubmit={(event) =>
            void form.handleSubmit((values) => {
              mutation.mutate(values);
            })(event)
          }
        >
          {/* Lets password managers tie the new password to this account. */}
          <input
            type="email"
            name="username"
            autoComplete="username"
            value={user.email}
            readOnly
            hidden
          />
          <CardContent className="flex flex-col gap-4">
            {formError ? (
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>{formError}</AlertDescription>
              </Alert>
            ) : null}
            <PasswordField
              control={form.control}
              name="current"
              label="Current password"
              autoComplete="current-password"
            />
            <PasswordField
              control={form.control}
              name="next"
              label="New password"
              autoComplete="new-password"
              description={PASSWORD_HINT}
            />
            <PasswordField
              control={form.control}
              name="confirm"
              label="Confirm new password"
              autoComplete="new-password"
            />
          </CardContent>
          <CardFooter className="mt-6 justify-end">
            <Button
              type="submit"
              className="w-full sm:w-auto"
              disabled={pending}
              aria-busy={pending}
            >
              {pending ? (
                <>
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                  Changing password
                </>
              ) : (
                "Change password"
              )}
            </Button>
          </CardFooter>
        </form>
      </Form>
    </Card>
  );
}
