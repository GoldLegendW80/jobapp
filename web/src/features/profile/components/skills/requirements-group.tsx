import { Plus } from "lucide-react";
import { useFieldArray, useWatch, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { WORDING } from "@/lib/wording";

import {
  GATES_MAX,
  NEW_GATE,
  type SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import { REQUIREMENTS_IN_WORDS } from "@/features/profile/helpers/skill-list-labels";
import { ITEM, ITEM_LIST } from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../about/folded-group";
import { RequirementFields } from "./requirement-fields";

/** The requirements the person lacks (gates) in words, a folded item of the Skills fields. */
export function Requirements({ form }: { form: UseFormReturn<SkillsForm> }) {
  const gates = useFieldArray({ control: form.control, name: "gates" });
  const names = useWatch({ control: form.control, name: "gates" })
    .map((gate) => gate.name.trim())
    .filter(Boolean);
  const add = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      aria-label="Add a requirement that lowers my fit"
      disabled={gates.fields.length >= GATES_MAX}
      onClick={() => {
        gates.append(
          { ...NEW_GATE, advanced_regex: false, terms: [] },
          { shouldFocus: true },
        );
      }}
    >
      <Plus aria-hidden="true" />
      {WORDING.addRequirement}
    </Button>
  );
  return (
    <FoldedGroup
      value="gates"
      title={REQUIREMENTS_IN_WORDS}
      summary={names.length > 0 ? names.join(", ") : WORDING.noneYet}
      description="Hard requirements you do not have, such as a PhD. A posting that asks for one loses its points."
      action={add}
    >
      {gates.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">{WORDING.noneYet}</p>
      ) : (
        <ol className={ITEM_LIST}>
          {gates.fields.map((item, index) => (
            <li key={item.id} className={ITEM}>
              <RequirementFields
                form={form}
                index={index}
                count={gates.fields.length}
                where={`requirement ${index + 1} that lowers my fit`}
                onMove={gates.move}
                onRemove={gates.remove}
              />
            </li>
          ))}
        </ol>
      )}
    </FoldedGroup>
  );
}
