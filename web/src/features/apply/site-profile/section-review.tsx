import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { errorDetail, describeError } from "@/lib/api";
import { ConfirmDialog } from "../confirm-dialog";
import {
  action,
  editField,
  screenshot,
  type ProfileField,
  type ProfileRun,
  type ProfileSection,
} from "./api";

const refusals: Record<string, string> = {
  email: "Your e-mail address can only be changed by you on the site.",
  security:
    "Passwords, verification and security settings can only be changed by you.",
  privacy:
    "Privacy, visibility and recruiter contact settings can only be changed by you.",
  consent:
    "Notifications, marketing, communities, consent and terms are left for you.",
  deletion:
    "The assistant never deletes or withdraws anything from your site profile.",
  control: "This setting is left for you to change on the site.",
  no_source:
    "Your profile has no factual source for this field, so it is left empty.",
  autosave_unsupported:
    "This control cannot be written with an atomic empty-only check, so it is left for you.",
  factcheck:
    "This text includes a fact that your stored profile does not support.",
};
const sourceLabels: Record<string, string> = {
  identity: "Identity",
  experience: "Experience",
  education: "Education",
  skills: "Skills",
  answers: "Answers",
  document: "Reference CV",
  person: "Your edit",
  left: "Left for you",
};
const valueText = (value: unknown) =>
  value == null || value === ""
    ? "Empty"
    : typeof value === "string"
      ? value
      : JSON.stringify(value);
const editText = (value: unknown) =>
  value == null || value === "" ? "" : valueText(value);
const canEditField = (field: ProfileField) =>
  field.kind !== "file" &&
  !(
    field.refusal &&
    ["email", "security", "privacy", "consent", "deletion", "control"].includes(
      field.refusal,
    )
  );

function FieldRow({
  field,
  run,
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
  run: ProfileRun;
  value: string;
  replace: boolean;
  pending: boolean;
  error: Error | null;
  onChange: (value: string) => void;
  onSave: () => void;
  onUndo: () => void;
  onReplace: (replace: boolean) => void;
}) {
  const mutable = canEditField(field) && !run.autosave;
  const dirty = value !== editText(field.value);
  return (
    <tr className="align-top border-b last:border-0">
      <th scope="row" className="p-3 text-left font-medium min-w-36">
        {field.label}
        {field.required && (
          <span className="block text-xs text-muted-foreground">
            Required by the site
          </span>
        )}
      </th>
      <td className="p-3 min-w-40 break-words">
        {valueText(field.current_value)}
      </td>
      <td className="p-3 min-w-56">
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
          <span className="break-words">{valueText(field.value)}</span>
        )}
        {field.refusal && (
          <p className="mt-2 text-sm text-muted-foreground">
            {refusals[field.refusal] ?? "This field is left for you to review."}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {errorDetail(error) ?? describeError(error)}
          </p>
        )}
      </td>
      <td className="p-3 min-w-28 text-sm">
        {field.source === "left"
          ? "Left for you"
          : field.source === "person"
            ? "Your edit"
            : (sourceLabels[field.source] ?? field.source)}
      </td>
      <td className="p-3 min-w-32">
        {field.current_value != null && field.current_value !== "" ? (
          mutable ? (
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                aria-label={`Replace ${field.label}`}
                checked={replace}
                disabled={pending}
                onChange={(event) => {
                  onReplace(event.target.checked);
                }}
              />
              Replace
            </label>
          ) : (
            "Current value kept"
          )
        ) : (
          "Empty field"
        )}
      </td>
    </tr>
  );
}

export function SectionReview({
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
  return (
    <SectionReviewContent
      key={`${run.id}:${section.section}`}
      run={run}
      section={section}
      update={update}
      refresh={refresh}
    />
  );
}

function SectionReviewContent({
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
  const [confirm, setConfirm] = useState<string | null>(null);
  if (confirm !== null && confirm !== section.values_hash) setConfirm(null);
  const [drafts, setDrafts] = useState<
    Record<string, { base: string; value: string }>
  >({});
  const currentDrafts = Object.fromEntries(
    Object.entries(drafts).filter(([key, draft]) =>
      section.fields.some(
        (field) =>
          field.field_key === key && editText(field.value) === draft.base,
      ),
    ),
  );
  if (Object.keys(currentDrafts).length !== Object.keys(drafts).length) {
    // A changed server value acknowledges or supersedes only that field's draft.
    setDrafts(currentDrafts);
  }
  const fieldValue = (field: ProfileField) => {
    const draft = drafts[field.field_key];
    return draft?.base === editText(field.value)
      ? draft.value
      : editText(field.value);
  };
  const edit = useMutation({
    mutationFn: ({
      field,
      changes,
    }: {
      field: string;
      changes: { value?: string; replace?: boolean };
    }) => editField(run.id, section.section, field, changes),
    onSuccess: update,
    onError: refresh,
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
        canEditField(field) && fieldValue(field) !== editText(field.value),
    );
  const approvalBlocked =
    !editable || !!section.error_code || unsaved || edit.isPending;
  const saveField = (
    field: ProfileField,
    changes: { value?: string; replace?: boolean },
  ) => {
    setConfirm(null);
    edit.mutate({ field: field.field_key, changes });
  };
  return (
    <Card aria-labelledby={`profile-section-${section.section}`}>
      <CardHeader>
        <CardTitle>
          <h2 id={`profile-section-${section.section}`} className="text-lg">
            {section.label}
          </h2>
        </CardTitle>
        <p className="text-sm capitalize" role="status">
          {section.state}
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {section.events.some((event) => event.kind === "page_addressed") && (
          <p className="text-sm text-muted-foreground">
            This page addressed an AI assistant. Those instructions were
            ignored.
          </p>
        )}
        {section.error_code && (
          <p role="alert" className="text-sm text-destructive">
            The section needs another review (
            {section.error_code.replaceAll("_", " ")}).
          </p>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          {section.screenshots
            .filter((shot) => shot.kind === "before" || shot.kind === "after")
            .map((shot) => (
              <figure key={shot.id} className="min-w-0">
                <a
                  href={screenshot(run.id, section.section, shot.id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    className="w-full rounded-md border"
                    src={screenshot(run.id, section.section, shot.id)}
                    alt={`${section.label}, ${shot.kind === "before" ? "before saving, with Save highlighted" : "after saving"}`}
                  />
                </a>
                <figcaption className="text-xs text-muted-foreground">
                  {shot.kind === "before" ? "Before saving" : "After saving"}
                </figcaption>
              </figure>
            ))}
        </div>
        <div className="max-w-full overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <caption className="p-3 text-left text-muted-foreground">
              Fields and their sources. Existing site values stay until you
              select Replace.
            </caption>
            <thead className="bg-muted/40">
              <tr>
                {[
                  "Field",
                  "Value on the site",
                  "Profile value or your edit",
                  "Source",
                  "Replace",
                ].map((label) => (
                  <th
                    className="p-3 text-left font-medium"
                    key={label}
                    scope="col"
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {section.fields.map((field) => (
                <FieldRow
                  key={field.field_key}
                  field={field}
                  run={editable ? run : { ...run, autosave: true }}
                  value={fieldValue(field)}
                  replace={
                    edit.isPending && edit.variables.field === field.field_key
                      ? (edit.variables.changes.replace ?? field.replace)
                      : field.replace
                  }
                  pending={edit.isPending || save.isPending}
                  error={
                    edit.variables?.field === field.field_key
                      ? edit.error
                      : null
                  }
                  onChange={(value) => {
                    setConfirm(null);
                    setDrafts((current) => ({
                      ...current,
                      [field.field_key]: { base: editText(field.value), value },
                    }));
                  }}
                  onSave={() => {
                    saveField(field, { value: fieldValue(field) });
                  }}
                  onUndo={() => {
                    setConfirm(null);
                    setDrafts((current) => {
                      return Object.fromEntries(
                        Object.entries(current).filter(
                          ([key]) => key !== field.field_key,
                        ),
                      );
                    });
                    if (edit.variables?.field === field.field_key) edit.reset();
                  }}
                  onReplace={(replace) => {
                    saveField(field, { replace });
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
        {save.error && (
          <p role="alert" className="text-sm text-destructive">
            {errorDetail(save.error) ?? describeError(save.error)} The section
            has been refreshed. Review the current values before approving
            again.
          </p>
        )}
        {editable && (
          <div className="space-y-2">
            {unsaved && (
              <p role="status" className="text-sm text-muted-foreground">
                Keep or undo your edits before approving this section.
              </p>
            )}
            {edit.isPending && (
              <p role="status" className="text-sm text-muted-foreground">
                Waiting for your field changes to be saved.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={save.isPending || approvalBlocked}
                onClick={() => {
                  if (!approvalBlocked) setConfirm(section.values_hash);
                }}
              >
                Approve section
              </Button>
              <Button
                variant="outline"
                disabled={save.isPending || edit.isPending}
                onClick={() => {
                  save.mutate("skip");
                }}
              >
                Skip section
              </Button>
            </div>
          </div>
        )}
        <ConfirmDialog
          ask={
            confirm === section.values_hash && !approvalBlocked
              ? {
                  title: `Save ${section.label}?`,
                  sentence: `Save the selected fields in ${section.label} on ${run.host}? Values on the site are replaced only where you chose Replace.`,
                  confirm: "Approve and save section",
                }
              : null
          }
          pending={save.isPending}
          onCancel={() => {
            setConfirm(null);
          }}
          onConfirm={() => {
            if (
              !save.isPending &&
              !approvalBlocked &&
              confirm === section.values_hash
            ) {
              save.mutate("approve");
            }
          }}
        />
      </CardContent>
    </Card>
  );
}
