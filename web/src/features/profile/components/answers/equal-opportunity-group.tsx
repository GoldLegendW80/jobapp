import { Plus } from "lucide-react";
import { useFieldArray, useWatch, type Control } from "react-hook-form";

import { Button } from "@/components/ui/button";

import { EEO_TOPICS } from "../../api/profile-api";
import type { AnswersForm } from "@/features/profile/helpers/forms";
import {
  Group,
  ItemControls,
  TextField,
} from "@/components/shared/profile/section-ui";
import { ChoiceField, type Choice } from "./choice-field";

const TOPIC_LABELS: Record<(typeof EEO_TOPICS)[number], string> = {
  gender: "Gender",
  ethnicity: "Ethnicity",
  race: "Race",
  disability: "Disability",
  veteran: "Veteran status",
  sexual_orientation: "Sexual orientation",
  age: "Age or date of birth",
};

const TOPICS: readonly Choice[] = EEO_TOPICS.map((topic) => ({
  value: topic,
  label: TOPIC_LABELS[topic],
}));

const POLICIES: readonly Choice[] = [
  { value: "decline", label: "Decline to answer" },
  { value: "leave", label: "Leave them to me" },
  { value: "stored", label: "Use the answers I store here" },
];

/** What to do with gender, ethnicity, disability and the like, and the answers stored for them. */
export function EqualOpportunityGroup({
  control,
}: {
  control: Control<AnswersForm>;
}) {
  const eeoAnswers = useFieldArray({ control, name: "eeo_answers" });
  const eeoPolicy = useWatch({ control, name: "eeo_policy" });
  const usedTopics = eeoAnswers.fields.map((f) => f.topic);
  const nextTopic = EEO_TOPICS.find((t) => !usedTopics.includes(t));
  return (
    <Group
      title="Equal opportunity questions"
      description="Gender, ethnicity, disability and the like. Nothing is ever inferred."
      action={
        eeoPolicy === "stored" ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={nextTopic === undefined}
            onClick={() => {
              if (nextTopic !== undefined) {
                eeoAnswers.append(
                  { topic: nextTopic, answer: "" },
                  { shouldFocus: true },
                );
              }
            }}
          >
            <Plus aria-hidden="true" />
            Add answer
          </Button>
        ) : undefined
      }
    >
      <ChoiceField
        control={control}
        name="eeo_policy"
        label="What to do"
        description="Decline picks the 'prefer not to say' option when a form offers one and leaves the field otherwise."
        options={POLICIES}
      />
      {eeoPolicy === "stored" ? (
        eeoAnswers.fields.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No answer stored yet. A topic without one is left to you.
          </p>
        ) : (
          <ol className="flex flex-col gap-3">
            {eeoAnswers.fields.map((item, index) => (
              <li
                key={item.id}
                className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-start"
              >
                <ChoiceField
                  control={control}
                  name={`eeo_answers.${index}.topic`}
                  label="Topic"
                  srSuffix={`${index + 1}`}
                  options={TOPICS}
                />
                <TextField
                  control={control}
                  name={`eeo_answers.${index}.answer`}
                  label="Answer"
                  srSuffix={`for topic ${index + 1}`}
                />
                <div className="sm:pt-6">
                  <ItemControls
                    index={index}
                    count={eeoAnswers.fields.length}
                    name={`answer ${index + 1}`}
                    onMove={eeoAnswers.move}
                    onRemove={eeoAnswers.remove}
                  />
                </div>
              </li>
            ))}
          </ol>
        )
      ) : null}
    </Group>
  );
}
