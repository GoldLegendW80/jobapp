import { Plus } from "lucide-react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { WORDING } from "@/lib/wording";

import {
  GATES_MAX,
  NEW_GATE,
  type SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import { Group } from "@/components/shared/profile/section-ui";
import { RequirementFields } from "./requirement-fields";

/** The hard requirements the person lacks (the api's gates), each taking points off a posting. */
export function RequirementsGroup({
  form,
}: {
  form: UseFormReturn<SkillsForm>;
}) {
  const gates = useFieldArray({ control: form.control, name: "gates" });
  return (
    <Group
      title={WORDING.requirementsNotMet}
      description="Hard requirements you do not have, such as a PhD or a clearance. When a posting names one of its terms, its points are taken off that posting's fit."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={gates.fields.length >= GATES_MAX}
          onClick={() => {
            gates.append({ ...NEW_GATE, terms: [] }, { shouldFocus: true });
          }}
        >
          <Plus aria-hidden="true" />
          {WORDING.addRequirement}
        </Button>
      }
    >
      {gates.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">{WORDING.noneYet}</p>
      ) : (
        <ol className="flex flex-col gap-4">
          {gates.fields.map((item, index) => (
            <li key={item.id} className="rounded-md border p-3 sm:p-4">
              <RequirementFields
                form={form}
                index={index}
                count={gates.fields.length}
                onMove={gates.move}
                onRemove={gates.remove}
              />
            </li>
          ))}
        </ol>
      )}
    </Group>
  );
}
