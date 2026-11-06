import { Plus } from "lucide-react";
import { useFieldArray, type Control } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";

import {
  AUTHORIZATIONS_MAX,
  type AnswersForm,
} from "@/features/profile/helpers/forms";
import {
  AreaField,
  Group,
  ItemControls,
  LabelText,
  TextField,
} from "@/components/shared/profile/section-ui";

/** What to answer, per country or region, when a form asks whether the person may work there. */
export function WorkAuthorizationGroup({
  control,
}: {
  control: Control<AnswersForm>;
}) {
  const countries = useFieldArray({ control, name: "work_authorization" });
  return (
    <Group
      title="Work authorisation"
      description="What to answer per country or region when a form asks whether you may work there."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={countries.fields.length >= AUTHORIZATIONS_MAX}
          onClick={() => {
            countries.append(
              { country: "", answer: "", needs_sponsorship: "" },
              { shouldFocus: true },
            );
          }}
        >
          <Plus aria-hidden="true" />
          Add country
        </Button>
      }
    >
      {countries.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No country yet.</p>
      ) : (
        <ol className="flex flex-col gap-4">
          {countries.fields.map((item, index) => {
            const n = index + 1;
            return (
              <li key={item.id} className="rounded-md border p-3 sm:p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h4 className="text-sm font-medium">Country {n}</h4>
                  <ItemControls
                    index={index}
                    count={countries.fields.length}
                    name={`country ${n}`}
                    onMove={countries.move}
                    onRemove={countries.remove}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextField
                    control={control}
                    name={`work_authorization.${index}.country`}
                    label="Country or region"
                    srSuffix={`${n}`}
                  />
                  <FormField
                    control={control}
                    name={`work_authorization.${index}.needs_sponsorship`}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          <LabelText
                            label="Needs visa sponsorship"
                            srSuffix={`in country ${n}`}
                          />
                        </FormLabel>
                        <FormControl>
                          <NativeSelect {...field}>
                            <option value="">Not specified</option>
                            <option value="no">No</option>
                            <option value="yes">Yes</option>
                          </NativeSelect>
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <AreaField
                    control={control}
                    name={`work_authorization.${index}.answer`}
                    label="Answer"
                    srSuffix={`for country ${n}`}
                    rows={2}
                    className="sm:col-span-2"
                  />
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Group>
  );
}
