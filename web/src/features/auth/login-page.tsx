import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
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
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { useTitle } from "@/lib/use-title";

import { login } from "./api";
import { useAuth } from "./auth-context";
import { AuthLayout } from "./auth-layout";
import { loginErrorMessage } from "./login-errors";

const schema = z.object({
  email: z
    .string()
    .refine((value) => value.trim() !== "", "Enter your email address.")
    .refine(
      (value) => value.trim() === "" || value.includes("@"),
      "Enter a valid email address.",
    ),
  password: z.string().min(1, "Enter your password."),
});
type Values = z.infer<typeof schema>;

export function LoginPage() {
  useTitle("Sign in");
  const { signIn, clearSignedOut } = useAuth();
  useEffect(() => {
    clearSignedOut();
  }, [clearSignedOut]);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });
  // Success stores the user; the PublicOnly guard then leaves for `next`.
  const mutation = useMutation({
    mutationFn: (values: Values) => login(values.email.trim(), values.password),
    onSuccess: signIn,
  });
  const pending = mutation.isPending || mutation.isSuccess;

  return (
    <AuthLayout>
      <Card>
        <CardHeader>
          <CardTitle>
            <h1 className="text-xl">Sign in</h1>
          </CardTitle>
          <CardDescription>
            Use the email address and password of your account.
          </CardDescription>
        </CardHeader>
        <Form {...form}>
          <form
            noValidate
            aria-label="Sign in"
            onSubmit={(event) =>
              void form.handleSubmit((values) => {
                mutation.mutate(values);
              })(event)
            }
          >
            <CardContent className="flex flex-col gap-4">
              {mutation.isError ? (
                <Alert variant="destructive">
                  <CircleAlert aria-hidden="true" />
                  <AlertDescription>
                    {loginErrorMessage(mutation.error)}
                  </AlertDescription>
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
                        autoComplete="username"
                        autoCapitalize="none"
                        autoCorrect="off"
                        spellCheck={false}
                        inputMode="email"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Password</FormLabel>
                    <FormControl>
                      <Input
                        type="password"
                        autoComplete="current-password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </CardContent>
            <CardFooter className="mt-6 flex flex-col items-stretch gap-4">
              <Button type="submit" disabled={pending} aria-busy={pending}>
                {pending ? (
                  <>
                    <LoaderCircle aria-hidden="true" className="animate-spin" />
                    Signing in
                  </>
                ) : (
                  "Sign in"
                )}
              </Button>
              <p className="text-center text-sm text-muted-foreground">
                No account yet? Ask the admin for an invite link.
              </p>
            </CardFooter>
          </form>
        </Form>
      </Card>
    </AuthLayout>
  );
}
