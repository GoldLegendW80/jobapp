import { Plus } from "lucide-react";
import { useFieldArray, useWatch, type Control } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";

import { EEO_TOPICS } from "../../../api/profile-api";
import type { AnswersForm } from "@/features/profile/helpers/forms";
import {
  ITEM,
  ITEM_LIST,
  ItemControls,
  LabelText,
  TextField,
} from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../folded-group";
import { ChoiceToggles } from "./choice-toggles";
import { labelOf, POLICIES } from "./choices";

const TOPIC_LABELS: Record<(typeof EEO_TOPICS)[number], string> = {
  gender: "Gender",
  ethnicity: "Ethnicity",
  race: "Race",
  disability: "Disability",
  veteran: "Veteran status",
  sexual_orientation: "Sexual orientation",
  age: "Age or date of birth",
};

/** What to do with gender, ethnicity, disability and the like, and the answers stored for them. */
export function EqualOpportunity({
  control,
}: {
  control: Control<AnswersForm>;
}) {
  const answers = useFieldArray({ control, name: "eeo_answers" });
  const policy = useWatch({ control, name: "eeo_policy" });
  const used = answers.fields.map((one) => one.topic);
  const nextTopic = EEO_TOPICS.find((topic) => !used.includes(topic));
  const stored = policy === "stored";
  return (
    <FoldedGroup
      value="eeo"
      title="Equal opportunity questions"
      summary={labelOf(POLICIES, policy)}
      description="Gender, ethnicity, disability and the like. Nothing is ever inferred."
      action={
        stored ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={nextTopic === undefined}
            onClick={() => {
              if (nextTopic !== undefined)
                answers.append(
                  { topic: nextTopic, answer: "" },
                  { shouldFocus: true },
                );
            }}
          >
            <Plus aria-hidden="true" />
            Add answer
          </Button>
        ) : undefined
      }
    >
      <ChoiceToggles
        control={control}
        name="eeo_policy"
        label="What to do"
        description="Decline picks the 'prefer not to say' option when a form offers one and leaves the field otherwise."
        options={POLICIES}
      />
      {!stored ? null : answers.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No answer stored yet. A topic without one is left to you.
        </p>
      ) : (
        <ol className={ITEM_LIST}>
          {answers.fields.map((item, index) => {
            const n = index + 1;
            return (
              <li
                key={item.id}
                className={`${ITEM} grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]`}
              >
                <FormField
                  control={control}
                  name={`eeo_answers.${index}.topic`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        <LabelText label="Topic" srSuffix={`${n}`} />
                      </FormLabel>
                      <FormControl>
                        <NativeSelect {...field}>
                          {EEO_TOPICS.map((topic) => (
                            <option key={topic} value={topic}>
                              {TOPIC_LABELS[topic]}
                            </option>
                          ))}
                        </NativeSelect>
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <TextField
                  control={control}
                  name={`eeo_answers.${index}.answer`}
                  label="Answer"
                  srSuffix={`for topic ${n}`}
                />
                <ItemControls
                  index={index}
                  count={answers.fields.length}
                  name={`answer ${n}`}
                  onMove={answers.move}
                  onRemove={answers.remove}
                />
              </li>
            );
          })}
        </ol>
      )}
    </FoldedGroup>
  );
}
