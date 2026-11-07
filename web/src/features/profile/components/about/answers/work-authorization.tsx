import { Plus } from "lucide-react";
import { useFieldArray, useWatch, type Control } from "react-hook-form";

import { Button } from "@/components/ui/button";

import {
  AUTHORIZATIONS_MAX,
  type AnswersForm,
} from "@/features/profile/helpers/forms";
import {
  AreaField,
  ITEM,
  ITEM_LIST,
  ItemControls,
  TextField,
} from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../folded-group";
import { ChoiceToggles } from "./choice-toggles";
import { YES_NO_NOT_SAID } from "./choices";

/** What to answer, per country or region, when a form asks whether the person may work there. */
export function WorkAuthorization({
  control,
}: {
  control: Control<AnswersForm>;
}) {
  const countries = useFieldArray({ control, name: "work_authorization" });
  const named = useWatch({ control, name: "work_authorization" })
    .map((one) => one.country.trim())
    .filter(Boolean);
  return (
    <FoldedGroup
      value="authorization"
      title="Work authorisation"
      summary={named.length > 0 ? named.join(", ") : "No country yet"}
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
        <ol className={ITEM_LIST}>
          {countries.fields.map((item, index) => {
            const n = index + 1;
            return (
              <li key={item.id} className={`${ITEM} flex flex-col gap-4`}>
                <div className="flex items-end justify-between gap-2">
                  <TextField
                    control={control}
                    name={`work_authorization.${index}.country`}
                    label="Country or region"
                    srSuffix={`${n}`}
                    className="min-w-0 flex-1"
                  />
                  <ItemControls
                    index={index}
                    count={countries.fields.length}
                    name={`country ${n}`}
                    onMove={countries.move}
                    onRemove={countries.remove}
                  />
                </div>
                <ChoiceToggles
                  control={control}
                  name={`work_authorization.${index}.needs_sponsorship`}
                  label="Needs visa sponsorship"
                  srSuffix={`in country ${n}`}
                  options={YES_NO_NOT_SAID}
                />
                <AreaField
                  control={control}
                  name={`work_authorization.${index}.answer`}
                  label="Answer"
                  srSuffix={`for country ${n}`}
                  rows={2}
                />
              </li>
            );
          })}
        </ol>
      )}
    </FoldedGroup>
  );
}
