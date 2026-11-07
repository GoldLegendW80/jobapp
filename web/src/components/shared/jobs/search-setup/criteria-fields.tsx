import { useId } from "react";
import type { UseFormReturn } from "react-hook-form";

import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import {
  CONTRACT_TYPES,
  REMOTE_STANCES,
} from "@/features/profile/api/profile-api";
import type { SearchForm } from "@/features/profile/helpers/editor-forms";
import { contractWord } from "@/lib/wording";

const REMOTE_STANCE_WORDS: Record<(typeof REMOTE_STANCES)[number], string> = {
  remote: "Remote only",
  hybrid: "Hybrid",
  office: "On site",
  any: "Any",
};

/** Under More options: the remote preference, the contract types and how recent a posting. */
export function CriteriaFields({ form }: { form: UseFormReturn<SearchForm> }) {
  const id = useId();
  return (
    <>
      <FormField
        control={form.control}
        name="remote_stance"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Remote preference</FormLabel>
            <FormControl>
              <NativeSelect {...field}>
                <option value="">Not set</option>
                {REMOTE_STANCES.map((stance) => (
                  <option key={stance} value={stance}>
                    {REMOTE_STANCE_WORDS[stance]}
                  </option>
                ))}
              </NativeSelect>
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="contract_types"
        render={({ field }) => (
          <FieldSet className="gap-2">
            <FieldLegend variant="label" className="mb-2">
              Contract types
            </FieldLegend>
            <FieldGroup
              data-slot="checkbox-group"
              className="flex-row flex-wrap gap-3"
            >
              {CONTRACT_TYPES.map((contract) => (
                <Field
                  key={contract}
                  orientation="horizontal"
                  className="w-auto gap-2"
                >
                  <Checkbox
                    id={`${id}-contract-${contract}`}
                    checked={field.value.includes(contract)}
                    onBlur={field.onBlur}
                    onCheckedChange={(checked) => {
                      // As the boxes of one name did: the ticked ones, in the order shown.
                      field.onChange(
                        CONTRACT_TYPES.filter((one) =>
                          one === contract
                            ? checked === true
                            : field.value.includes(one),
                        ),
                      );
                    }}
                  />
                  <FieldLabel
                    htmlFor={`${id}-contract-${contract}`}
                    className="font-normal"
                  >
                    {contractWord(contract)}
                  </FieldLabel>
                </Field>
              ))}
            </FieldGroup>
          </FieldSet>
        )}
      />
      <FormField
        control={form.control}
        name="days"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Posted within the last days</FormLabel>
            <FormControl>
              <Input
                {...field}
                type="number"
                min={1}
                max={365}
                className="max-w-32"
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </>
  );
}
