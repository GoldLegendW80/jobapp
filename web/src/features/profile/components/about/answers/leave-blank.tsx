import { Plus } from "lucide-react";
import { useFieldArray, type Control } from "react-hook-form";

import { Button } from "@/components/ui/button";

import {
  LEAVE_BLANK_MAX,
  type AnswersForm,
} from "@/features/profile/helpers/forms";
import {
  ITEM,
  ITEM_LIST,
  ItemControls,
  TextField,
} from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../folded-group";

/** The questions the person always answers on a form: the apply agent leaves them blank. */
export function LeaveBlank({ control }: { control: Control<AnswersForm> }) {
  const blanks = useFieldArray({ control, name: "leave_blank" });
  const count = blanks.fields.length;
  return (
    <FoldedGroup
      value="leave-blank"
      title="Leave blank"
      summary={
        count === 0
          ? "Nothing listed"
          : `${count} ${count === 1 ? "question" : "questions"}`
      }
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
        <ol className={ITEM_LIST}>
          {blanks.fields.map((item, index) => (
            <li
              key={item.id}
              className={`${ITEM} flex items-end justify-between gap-2`}
            >
              <TextField
                control={control}
                name={`leave_blank.${index}.value`}
                label="Question"
                srSuffix={`${index + 1} to leave blank`}
                className="min-w-0 flex-1"
              />
              <ItemControls
                index={index}
                count={blanks.fields.length}
                name={`question ${index + 1}`}
                onMove={blanks.move}
                onRemove={blanks.remove}
              />
            </li>
          ))}
        </ol>
      )}
    </FoldedGroup>
  );
}
