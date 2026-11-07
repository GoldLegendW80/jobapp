import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldTitle } from "@/components/ui/field";
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
import { SettingsSection } from "@/components/shared/profile/settings-frame";
import { message } from "@/features/admin/helpers/errors";
import type { User } from "@/features/auth/api/auth-api";
import { useRenameMe } from "@/features/auth/api/use-me";
import { cleanName, nameProblem } from "@/features/auth/helpers/password-rules";

import { fieldError } from "../helpers/field-error";

const schema = z.object({ name: z.string() }).superRefine((values, ctx) => {
  const problem = nameProblem(values.name);
  if (problem)
    ctx.addIssue({ code: "custom", path: ["name"], message: problem });
});
type Values = z.infer<typeof schema>;

/** "Profile name": the name shown in the header menu and to the admin. */
export function NameCard({ user }: { user: User }) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: user.name },
  });
  const [formError, setFormError] = useState<string>();
  const mutation = useRenameMe({
    onMutate: () => {
      setFormError(undefined);
    },
    onSuccess: (saved) => {
      form.reset({ name: saved.name });
      toast.success("Name saved.");
    },
    onError: (error) => {
      const found = fieldError(error, ["name"] as const);
      if (found) {
        form.setError(
          "name",
          { message: found.message },
          { shouldFocus: true },
        );
        return;
      }
      setFormError(message(error));
    },
  });
  const typed = useWatch({ control: form.control, name: "name" });
  const unchanged = cleanName(typed) === user.name;

  return (
    <SettingsSection
      title="Profile name"
      description="How you appear in the header and to the admin."
    >
      <Form {...form}>
        <form
          noValidate
          aria-label="Profile name"
          className="flex max-w-md flex-col gap-4"
          onSubmit={(event) =>
            void form.handleSubmit((values) => {
              mutation.mutate(cleanName(values.name));
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
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Name</FormLabel>
                <FormControl>
                  <Input autoComplete="name" maxLength={1024} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Field className="gap-2">
            <FieldTitle>Email</FieldTitle>
            <FieldDescription className="break-all">
              {user.email}
            </FieldDescription>
          </Field>
          <Button
            type="submit"
            className="w-fit"
            disabled={mutation.isPending || unchanged}
            aria-busy={mutation.isPending}
          >
            {mutation.isPending ? (
              <>
                <Spinner aria-hidden="true" role={undefined} />
                Saving
              </>
            ) : (
              "Save name"
            )}
          </Button>
        </form>
      </Form>
    </SettingsSection>
  );
}
