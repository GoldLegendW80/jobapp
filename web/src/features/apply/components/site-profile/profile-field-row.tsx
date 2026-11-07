import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { FieldError } from "@/components/ui/field";
import { Label } from "@/components/ui/label";
import { TableCell, TableHead, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { describeError, errorDetail } from "@/lib/api";

import type { ProfileField } from "../../api/site-profile-api";
import {
  canEditField,
  fieldEditText,
  fieldSourceLabel,
  fieldValueText,
  refusalSentence,
} from "@/features/apply/helpers/site-profile-fields";

/**
 * One field of a site profile section: its label, the value on the site, the profile's value or
 * the person's edit (with Keep edit and Undo where it may change), why it was left, its source,
 * and Replace for a site value that is not empty. `editable` false shows it read only.
 */
export function ProfileFieldRow({
  field,
  editable,
  value,
  replace,
  pending,
  error,
  onChange,
  onSave,
  onUndo,
  onReplace,
}: {
  field: ProfileField;
  editable: boolean;
  value: string;
  replace: boolean;
  pending: boolean;
  error: Error | null;
  onChange: (value: string) => void;
  onSave: () => void;
  onUndo: () => void;
  onReplace: (replace: boolean) => void;
}) {
  const mutable = canEditField(field) && editable;
  const dirty = value !== fieldEditText(field.value);
  return (
    <TableRow className="align-top">
      <TableHead className="p-3 sm:min-w-36">
        {field.label}
        {field.required && (
          <span className="block text-xs text-muted-foreground">
            Required by the site
          </span>
        )}
      </TableHead>
      <TableCell
        label="Value on the site"
        className="p-3 sm:min-w-40 break-words"
      >
        {fieldValueText(field.current_value)}
      </TableCell>
      <TableCell
        label="Profile value or your edit"
        className="p-3 sm:min-w-56 max-sm:items-start"
      >
        <div className="min-w-0">
          {mutable ? (
            <div className="space-y-2">
              <Textarea
                aria-label={`Edit ${field.label}`}
                value={value}
                disabled={pending}
                onChange={(event) => {
                  onChange(event.target.value);
                }}
                rows={2}
              />
              <Button
                size="sm"
                variant="outline"
                disabled={pending || !dirty}
                onClick={onSave}
              >
                Keep edit
              </Button>
              {dirty && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={pending}
                  onClick={onUndo}
                >
                  Undo
                </Button>
              )}
            </div>
          ) : (
            <span className="break-words">{fieldValueText(field.value)}</span>
          )}
          {field.refusal && (
            <p className="mt-2 text-sm text-muted-foreground">
              {refusalSentence(field.refusal)}
            </p>
          )}
          {error && (
            <FieldError>
              {errorDetail(error) ?? describeError(error)}
            </FieldError>
          )}
        </div>
      </TableCell>
      <TableCell label="Source" className="p-3 sm:min-w-28 text-sm">
        {fieldSourceLabel(field.source)}
      </TableCell>
      <TableCell label="Replace" className="p-3 sm:min-w-32">
        {field.current_value != null && field.current_value !== "" ? (
          mutable ? (
            <Label className="font-normal">
              <Checkbox
                aria-label={`Replace ${field.label}`}
                checked={replace}
                disabled={pending}
                onCheckedChange={(checked) => {
                  onReplace(checked === true);
                }}
              />
              Replace
            </Label>
          ) : (
            "Current value kept"
          )
        ) : (
          "Empty field"
        )}
      </TableCell>
    </TableRow>
  );
}
