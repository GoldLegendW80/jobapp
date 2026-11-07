import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import type { Control, FieldPath, FieldValues } from "react-hook-form";

import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";

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
          <InputGroup data-disabled={disabled}>
            <FormControl>
              <InputGroupInput
                type={shown ? "text" : "password"}
                autoComplete={autoComplete}
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                disabled={disabled}
                {...field}
              />
            </FormControl>
            <InputGroupAddon align="inline-end">
              <InputGroupButton
                size="icon-sm"
                className="hover:text-foreground"
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
              </InputGroupButton>
            </InputGroupAddon>
          </InputGroup>
          {description ? (
            <FormDescription>{description}</FormDescription>
          ) : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
