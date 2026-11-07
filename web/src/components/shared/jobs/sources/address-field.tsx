import { Plus } from "lucide-react";

import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Spinner } from "@/components/ui/spinner";

import { BLOCK } from "../setup-grid";

/** The box for any address with its Add button inside, and what went wrong or the hint under it. */
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
      <InputGroup data-disabled={disabled || undefined}>
        <InputGroupInput
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
        <InputGroupAddon align="inline-end">
          <InputGroupButton
            disabled={disabled || !value.trim()}
            onClick={onAdd}
          >
            {adding ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : (
              <Plus aria-hidden="true" />
            )}
            {adding ? "Checking" : "Add"}
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
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
