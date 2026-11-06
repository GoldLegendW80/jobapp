import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import {
  action,
  editField,
  type ProfileField,
  type ProfileRun,
  type ProfileSection,
} from "./site-profile-api";
import {
  canEditField,
  fieldEditText,
  newerTime,
} from "../helpers/site-profile-fields";

type FieldChanges = { value?: string; replace?: boolean };
type FieldEdit = {
  field: string;
  changes: FieldChanges;
  acknowledged?: { field: ProfileField; updatedAt: string };
};

/**
 * The review of one section of a site profile run: the person's drafts of its fields, an edit
 * kept or a Replace ticked (one at a time, until the run shows it), and Approve (behind a
 * confirmation bound to the values the person saw) or Skip. A server value that moved drops only
 * that field's draft. `update` takes the run an answer returns; `refresh` reads it again.
 */
export function useSectionReview({
  run,
  section,
  update,
  refresh,
}: {
  run: ProfileRun;
  section: ProfileSection;
  update: (run: ProfileRun) => void | Promise<void>;
  refresh: () => void;
}) {
  // The values hash the confirmation was opened for: a moved section closes it.
  const [confirm, setConfirm] = useState<string | null>(null);
  if (confirm !== null && confirm !== section.values_hash) setConfirm(null);

  const [drafts, setDrafts] = useState<
    Record<string, { base: string; value: string }>
  >({});
  const currentDrafts = Object.fromEntries(
    Object.entries(drafts).filter(([key, draft]) =>
      section.fields.some(
        (field) =>
          field.field_key === key && fieldEditText(field.value) === draft.base,
      ),
    ),
  );
  if (Object.keys(currentDrafts).length !== Object.keys(drafts).length) {
    // A changed server value acknowledges or supersedes only that field's draft.
    setDrafts(currentDrafts);
  }
  const fieldValue = (field: ProfileField) => {
    const draft = drafts[field.field_key];
    return draft?.base === fieldEditText(field.value)
      ? draft.value
      : fieldEditText(field.value);
  };

  const [pendingEdit, setPendingEdit] = useState<FieldEdit | null>(null);
  const acknowledged = pendingEdit?.acknowledged;
  if (
    acknowledged &&
    (newerTime(run.updated_at, acknowledged.updatedAt) ||
      section.fields.some(
        (field) =>
          field.field_key === pendingEdit.field &&
          field.replace === acknowledged.field.replace &&
          fieldEditText(field.value) ===
            fieldEditText(acknowledged.field.value),
      ))
  ) {
    setPendingEdit(null);
  }

  const edit = useMutation({
    mutationFn: ({ field, changes }: FieldEdit) =>
      editField(run.id, section.section, field, changes),
    onSuccess: async (value, operation) => {
      await update(value);
      const field = value.sections
        .find((current) => current.section === section.section)
        ?.fields.find((current) => current.field_key === operation.field);
      setPendingEdit((current) =>
        current === operation && field
          ? {
              ...operation,
              acknowledged: { field, updatedAt: value.updated_at },
            }
          : current,
      );
    },
    onError: (_error, operation) => {
      setPendingEdit((current) => (current === operation ? null : current));
      refresh();
    },
  });
  const save = useMutation({
    mutationFn: (operation: "approve" | "skip") =>
      action(
        run.id,
        `sections/${encodeURIComponent(section.section)}/${operation}`,
        operation === "approve"
          ? { values_hash: section.values_hash }
          : undefined,
      ),
    onSuccess: (value) => {
      setConfirm(null);
      return update(value);
    },
    onError: () => {
      setConfirm(null);
      refresh();
    },
  });

  const editable = section.state === "ready" && run.state === "ready";
  const unsaved =
    editable &&
    !run.autosave &&
    section.fields.some(
      (field) =>
        canEditField(field) && fieldValue(field) !== fieldEditText(field.value),
    );
  const editPending = pendingEdit !== null || edit.isPending;
  const approvalBlocked =
    !editable || !!section.error_code || unsaved || editPending;

  const saveField = (field: ProfileField, changes: FieldChanges) => {
    if (editPending || save.isPending) return;
    const operation = { field: field.field_key, changes };
    setConfirm(null);
    setPendingEdit(operation);
    edit.mutate(operation);
  };

  return {
    editable,
    unsaved,
    editPending,
    approvalBlocked,
    save,
    fieldValue,
    replaceOf: (field: ProfileField) =>
      pendingEdit?.field === field.field_key
        ? (pendingEdit.changes.replace ?? field.replace)
        : field.replace,
    errorOf: (field: ProfileField) =>
      edit.variables?.field === field.field_key ? edit.error : null,
    changeField: (field: ProfileField, value: string) => {
      setConfirm(null);
      setDrafts((current) => ({
        ...current,
        [field.field_key]: { base: fieldEditText(field.value), value },
      }));
    },
    keepEdit: (field: ProfileField) => {
      saveField(field, { value: fieldValue(field) });
    },
    undoEdit: (field: ProfileField) => {
      setConfirm(null);
      setDrafts((current) =>
        Object.fromEntries(
          Object.entries(current).filter(([key]) => key !== field.field_key),
        ),
      );
      if (edit.variables?.field === field.field_key) edit.reset();
    },
    replaceField: (field: ProfileField, replace: boolean) => {
      saveField(field, { replace });
    },
    /** The confirmation of Approve is open, for the values the person sees. */
    confirming: confirm === section.values_hash && !approvalBlocked,
    askApprove: () => {
      if (!approvalBlocked) setConfirm(section.values_hash);
    },
    closeConfirm: () => {
      setConfirm(null);
    },
    approve: () => {
      if (
        !save.isPending &&
        !approvalBlocked &&
        confirm === section.values_hash
      )
        save.mutate("approve");
    },
    skip: () => {
      save.mutate("skip");
    },
  };
}
