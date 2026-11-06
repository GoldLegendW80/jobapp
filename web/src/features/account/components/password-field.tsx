import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import type { Control, FieldPath, FieldValues } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";

/**
 * A password input with its label, optional hint, error and a show toggle: a real button with
 * aria-pressed, reached with Tab right after the input. Its name stays "Show <label>" and the
 * pressed state says whether the text is visible.
 */
export function PasswordField<T extends FieldValues>({
  control,
  name,
  label,
  autoComplete,
  description,
  disabled,
}: {
  control: Control<T>;
  name: FieldPath<T>;
  label: string;
  autoComplete: "current-password" | "new-password";
  description?: string;
  disabled?: boolean;
}) {
  const [shown, setShown] = useState(false);
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <div className="relative">
            <FormControl>
              <Input
                type={shown ? "text" : "password"}
                autoComplete={autoComplete}
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                className="pr-11"
                disabled={disabled}
                {...field}
              />
            </FormControl>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute top-0 right-0 text-muted-foreground hover:text-foreground"
              aria-label={`Show ${label.toLowerCase()}`}
              aria-pressed={shown}
              disabled={disabled}
              onClick={() => {
                setShown((value) => !value);
              }}
            >
              {shown ? (
                <EyeOff aria-hidden="true" />
              ) : (
                <Eye aria-hidden="true" />
              )}
            </Button>
          </div>
          {description ? (
            <FormDescription>{description}</FormDescription>
          ) : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
