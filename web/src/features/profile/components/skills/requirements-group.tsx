import { Plus } from "lucide-react";
import { useFieldArray, useWatch, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { WORDING } from "@/lib/wording";

import {
  GATES_MAX,
  NEW_GATE,
  type SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import {
  REQUIREMENTS_IN_WORDS,
  type SkillMode,
} from "@/features/profile/helpers/skill-list-labels";
import { Group, ITEM, ITEM_LIST } from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../about/folded-group";
import { RequirementFields } from "./requirement-fields";

/** The requirements the person lacks written as regular expressions (gates), each taking points off. */
export function RequirementsGroup({
  form,
}: {
  form: UseFormReturn<SkillsForm>;
}) {
  return <Requirements form={form} mode="regex" />;
}

/**
 * The requirements the person lacks (gates), in words in About you or as regular expressions in
 * Advanced matching: a Group of its own there, a folded item of the Skills fields here.
 */
export function Requirements({
  form,
  mode,
}: {
  form: UseFormReturn<SkillsForm>;
  mode: SkillMode;
}) {
  const gates = useFieldArray({ control: form.control, name: "gates" });
  const names = useWatch({ control: form.control, name: "gates" })
    .map((gate) => gate.name.trim())
    .filter(Boolean);
  const words = mode === "words";
  const add = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      aria-label={words ? "Add a requirement that lowers my fit" : undefined}
      disabled={gates.fields.length >= GATES_MAX}
      onClick={() => {
        gates.append(
          { ...NEW_GATE, advanced_regex: !words, terms: [] },
          { shouldFocus: true },
        );
      }}
    >
      <Plus aria-hidden="true" />
      {WORDING.addRequirement}
    </Button>
  );
  const list =
    gates.fields.length === 0 ? (
      <p className="text-sm text-muted-foreground">{WORDING.noneYet}</p>
    ) : (
      <ol className={ITEM_LIST}>
        {gates.fields.map((item, index) => (
          <li key={item.id} className={ITEM}>
            <RequirementFields
              form={form}
              index={index}
              count={gates.fields.length}
              where={
                words
                  ? `requirement ${index + 1} that lowers my fit`
                  : `requirement ${index + 1}`
              }
              regexSwitch={!words}
              onMove={gates.move}
              onRemove={gates.remove}
            />
          </li>
        ))}
      </ol>
    );
  return words ? (
    <FoldedGroup
      value="gates"
      title={REQUIREMENTS_IN_WORDS}
      summary={names.length > 0 ? names.join(", ") : WORDING.noneYet}
      description="Hard requirements you do not have, such as a PhD. A posting that asks for one loses its points."
      action={add}
    >
      {list}
    </FoldedGroup>
  ) : (
    <Group
      title={WORDING.requirementsNotMet}
      description="Hard requirements you do not have, such as a PhD or a clearance. When a posting matches one of its expressions, its points are taken off that posting's fit."
      action={add}
    >
      {list}
    </Group>
  );
}
