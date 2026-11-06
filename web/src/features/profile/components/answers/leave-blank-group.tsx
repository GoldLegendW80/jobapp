import { Plus } from "lucide-react";
import { useFieldArray, type Control } from "react-hook-form";

import { Button } from "@/components/ui/button";

import {
  LEAVE_BLANK_MAX,
  type AnswersForm,
} from "@/features/profile/helpers/forms";
import {
  Group,
  ItemControls,
  TextField,
} from "@/components/shared/profile/section-ui";

/** The questions the person always answers on a form: the apply agent leaves them blank. */
export function LeaveBlankGroup({
  control,
}: {
  control: Control<AnswersForm>;
}) {
  const blanks = useFieldArray({ control, name: "leave_blank" });
  return (
    <Group
      title="Leave blank"
      description="Questions forms ask that you always answer yourself, such as voluntary self identification."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={blanks.fields.length >= LEAVE_BLANK_MAX}
          onClick={() => {
            blanks.append({ value: "" }, { shouldFocus: true });
          }}
        >
          <Plus aria-hidden="true" />
          Add question
        </Button>
      }
    >
      {blanks.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing listed.</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {blanks.fields.map((item, index) => (
            <li
              key={item.id}
              className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-start"
            >
              <TextField
                control={control}
                name={`leave_blank.${index}.value`}
                label="Question"
                srSuffix={`${index + 1} to leave blank`}
              />
              <div className="sm:pt-6">
                <ItemControls
                  index={index}
                  count={blanks.fields.length}
                  name={`question ${index + 1}`}
                  onMove={blanks.move}
                  onRemove={blanks.remove}
                />
              </div>
            </li>
          ))}
        </ol>
      )}
    </Group>
  );
}
