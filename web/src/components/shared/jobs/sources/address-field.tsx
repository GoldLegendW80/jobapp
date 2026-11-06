import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";

import { BLOCK } from "../setup-grid";

/** The box for any address, its Add button, and what went wrong or the hint under it. */
export function AddressField({
  id,
  value,
  problem,
  adding,
  disabled,
  onChange,
  onAdd,
}: {
  id: string;
  value: string;
  problem: string | null;
  /** The address is being checked. */
  adding: boolean;
  disabled: boolean;
  onChange: (text: string) => void;
  onAdd: () => void;
}) {
  return (
    <Field className={BLOCK}>
      <FieldLabel htmlFor={`${id}-address`} className="leading-none">
        Website address
      </FieldLabel>
      {/* One row at every width, as the word lists' box and its Add below. */}
      <div className="flex gap-2">
        <Input
          id={`${id}-address`}
          type="url"
          inputMode="url"
          autoComplete="url"
          maxLength={2000}
          value={value}
          disabled={disabled}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-problem` : `${id}-hint`}
          onChange={(event) => {
            onChange(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onAdd();
            }
          }}
          placeholder="https://jobs.ashbyhq.com/doctolib"
        />
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !value.trim()}
          onClick={onAdd}
        >
          {adding ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <Plus aria-hidden="true" />
          )}
          {adding ? "Checking" : "Add"}
        </Button>
      </div>
      {problem ? (
        <FieldError id={`${id}-problem`}>{problem}</FieldError>
      ) : (
        <FieldDescription id={`${id}-hint`} className="text-xs leading-4">
          Websites are searched by the assistant with your model key. Job feeds
          and company job boards are read directly, at no cost.
        </FieldDescription>
      )}
    </Field>
  );
}
