import { CircleAlert, Plus } from "lucide-react";
import {
  Fragment,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { message } from "@/features/admin/helpers/errors";

import { LoadError } from "@/components/blocks/load-error";
import { ConfirmDialog } from "@/components/shared/apply/confirm-dialog";
import { SettingsSection } from "@/components/shared/profile/settings-frame";
import {
  capSentence,
  days,
  LABEL_MAX,
  readCap,
  settingsRefusalOf,
  type Cap,
  type CapDraft,
  type CapProblems,
} from "../api/settings-api";
import { useCaps, useSaveCaps } from "../api/use-caps";

const toDraft = ({
  label,
  max_count,
  window_days,
  same_role_days,
}: CapDraft): CapDraft => ({ label, max_count, window_days, same_role_days });

type Change = { items: CapDraft[]; revision: string; done: string };

/**
 * "Company caps": the person's own limits per company ("Example Robotics: at most 3 applications
 * in 90 days, and 180 days before the same role") with what each counts now. Add, edit and remove
 * each save the whole list with the revision the page showed: a list changed elsewhere answers
 * 409 `stale`, and the page reloads it and says so; a list the api cannot save answers 422 with
 * its sentence.
 */
export function CapsEditor() {
  const query = useCaps();
  const [adding, setAdding] = useState(false);
  // The label of the row open for editing: labels are unique per company and survive a save.
  const [editing, setEditing] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Cap | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const save = useSaveCaps<Change>({
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (_saved, change) => {
      setAdding(false);
      setEditing(null);
      setRemoving(null);
      toast.success(change.done);
    },
    onError: (error) => {
      setRemoving(null);
      setNotice(settingsRefusalOf(error)?.detail ?? message(error));
    },
  });

  const caps = query.data;
  const change = (items: CapDraft[], done: string) => {
    if (caps) save.mutate({ items, revision: caps.revision, done });
  };

  return (
    <SettingsSection
      title="Company caps"
      description="Your own limits per company. An application the app submitted, or may have submitted, counts toward its cap."
      actions={
        caps && !adding ? (
          <Button
            variant="outline"
            disabled={save.isPending}
            onClick={() => {
              setEditing(null);
              setAdding(true);
            }}
          >
            <Plus aria-hidden="true" />
            Add a cap
          </Button>
        ) : null
      }
    >
      {notice ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}
      {query.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading your caps
        </p>
      ) : query.isError ? (
        <LoadError query={query}>Your caps could not be loaded.</LoadError>
      ) : (
        <>
          {query.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No cap yet. Add one to limit how often the app applies to a
              company.
            </p>
          ) : (
            <ItemGroup aria-label="Your caps">
              {query.data.items.map((cap, index) => (
                // Every save replaces the rows and their ids, so a row is keyed on its label.
                <Fragment key={cap.label}>
                  {index > 0 ? <ItemSeparator /> : null}
                  {editing === cap.label ? (
                    <div role="listitem" className="py-3">
                      <CapForm
                        formLabel={`Edit the cap of ${cap.label}`}
                        initial={cap}
                        pending={save.isPending}
                        submit="Save cap"
                        onCancel={() => {
                          setEditing(null);
                        }}
                        onSave={(draft) => {
                          change(
                            query.data.items.map((one) =>
                              one.label === cap.label ? draft : toDraft(one),
                            ),
                            "Cap saved.",
                          );
                        }}
                      />
                    </div>
                  ) : (
                    <Item
                      role="listitem"
                      size="sm"
                      className="flex-col items-stretch gap-2 rounded-none border-0 px-0 sm:flex-row sm:items-center"
                    >
                      <ItemContent className="min-w-0 gap-0.5">
                        <ItemTitle className="break-words">
                          {capSentence(cap)}
                        </ItemTitle>
                        <ItemDescription>
                          {cap.used} of {cap.max_count} used in the last{" "}
                          {days(cap.window_days)}
                        </ItemDescription>
                      </ItemContent>
                      <ItemActions className="shrink-0">
                        <Button
                          variant="outline"
                          size="sm"
                          aria-label={`Edit the cap of ${cap.label}`}
                          disabled={save.isPending}
                          onClick={() => {
                            setAdding(false);
                            setEditing(cap.label);
                          }}
                        >
                          Edit
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          aria-label={`Remove the cap of ${cap.label}`}
                          disabled={save.isPending}
                          onClick={() => {
                            setRemoving(cap);
                          }}
                        >
                          Remove
                        </Button>
                      </ItemActions>
                    </Item>
                  )}
                </Fragment>
              ))}
            </ItemGroup>
          )}
          {adding ? (
            <CapForm
              formLabel="New cap"
              pending={save.isPending}
              submit="Add cap"
              onCancel={() => {
                setAdding(false);
              }}
              onSave={(draft) => {
                change([...query.data.items.map(toDraft), draft], "Cap added.");
              }}
            />
          ) : null}
        </>
      )}
      <ConfirmDialog
        ask={
          removing
            ? {
                title: "Remove this cap?",
                sentence: `${capSentence(removing)}. Once removed, this limit no longer holds back an approval.`,
                confirm: "Remove cap",
              }
            : null
        }
        pending={save.isPending}
        onCancel={() => {
          setRemoving(null);
        }}
        onConfirm={() => {
          if (!removing || !caps) return;
          change(
            caps.items
              .filter((one) => one.label !== removing.label)
              .map(toDraft),
            "Cap removed.",
          );
        }}
      />
    </SettingsSection>
  );
}

const FIELDS = [
  { name: "label", label: "Company" },
  { name: "max_count", label: "At most this many applications" },
  { name: "window_days", label: "In this many days" },
  {
    name: "same_role_days",
    label: "Days before the same role again (optional)",
  },
] as const;

function CapForm({
  formLabel,
  initial,
  pending,
  submit,
  onCancel,
  onSave,
}: {
  formLabel: string;
  initial?: CapDraft;
  pending: boolean;
  submit: string;
  onCancel: () => void;
  onSave: (draft: CapDraft) => void;
}) {
  const id = useId();
  const first = useRef<HTMLInputElement>(null);
  const [fields, setFields] = useState({
    label: initial?.label ?? "",
    max_count: initial ? String(initial.max_count) : "",
    window_days: initial ? String(initial.window_days) : "",
    same_role_days: initial?.same_role_days
      ? String(initial.same_role_days)
      : "",
  });
  const [problems, setProblems] = useState<CapProblems>({});

  useEffect(() => {
    first.current?.focus();
  }, []);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const read = readCap(fields);
    if ("problems" in read) {
      setProblems(read.problems);
      const wrong = FIELDS.find((field) => read.problems[field.name]);
      if (wrong) document.getElementById(`${id}-${wrong.name}`)?.focus();
      return;
    }
    setProblems({});
    onSave(read.draft);
  };

  return (
    <form
      noValidate
      aria-label={formLabel}
      className="flex max-w-2xl flex-col gap-4"
      onSubmit={onSubmit}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {FIELDS.map((field) => {
          const problem = problems[field.name];
          const inputId = `${id}-${field.name}`;
          return (
            <Field
              key={field.name}
              className={
                field.name === "label" ? "gap-2 sm:col-span-2" : "gap-2"
              }
            >
              <FieldLabel htmlFor={inputId}>{field.label}</FieldLabel>
              <Input
                ref={field.name === "label" ? first : undefined}
                id={inputId}
                {...(field.name === "label"
                  ? { autoComplete: "organization", maxLength: LABEL_MAX }
                  : { inputMode: "numeric" as const, maxLength: 4 })}
                value={fields[field.name]}
                aria-invalid={problem ? true : undefined}
                aria-describedby={problem ? `${inputId}-problem` : undefined}
                onChange={(event) => {
                  setFields((now) => ({
                    ...now,
                    [field.name]: event.target.value,
                  }));
                }}
              />
              {problem ? (
                <FieldError id={`${inputId}-problem`}>{problem}</FieldError>
              ) : null}
            </Field>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={pending} aria-busy={pending}>
          {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
          {submit}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={pending}
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
