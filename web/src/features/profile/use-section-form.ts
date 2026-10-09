import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type BaseSyntheticEvent } from "react";
import {
  useForm,
  type DefaultValues,
  type FieldValues,
  type Path,
  type UseFormReturn,
} from "react-hook-form";
import { toast } from "sonner";
import type { z } from "zod";

import { describeError } from "@/lib/api";

import {
  conflictOf,
  profileKey,
  saveSection,
  type Conflict,
  type Profile,
  type SectionData,
  type SectionName,
} from "./api";
import { serverFieldError, validationDetail, type ListRules } from "./errors";
import { SECTION_LABELS } from "./labels";

export interface SectionFormOptions<
  S extends SectionName,
  F extends FieldValues,
> {
  section: S;
  profile: Profile;
  schema: z.ZodType<F, F>;
  toForm: (data: SectionData[S]) => F;
  fromForm: (values: F) => SectionData[S];
  rules?: ListRules;
  onDirtyChange: (dirty: boolean) => void;
}

export interface SectionForm<F extends FieldValues> {
  form: UseFormReturn<F>;
  onSubmit: (event?: BaseSyntheticEvent) => Promise<void>;
  saving: boolean;
  formError: string | undefined;
  /** a save answered 409: the page shows the conflict dialog */
  conflict: boolean;
  /** keep these values and base the next save on the current revision (it then replaces it) */
  keepEditing: () => void;
  /** throw these values away and show the current version */
  reload: () => void;
}

/**
 * One editable section: the form, its Save (PUT with the revision the form was loaded from),
 * 422 errors on the field the api names, the 409 dialog, the toast, and the dirty flag the page
 * uses for its unsaved-changes guard.
 */
export function useSectionForm<S extends SectionName, F extends FieldValues>({
  section,
  profile,
  schema,
  toForm,
  fromForm,
  rules,
  onDirtyChange,
}: SectionFormOptions<S, F>): SectionForm<F> {
  const client = useQueryClient();
  const form = useForm<F>({
    resolver: zodResolver(schema),
    defaultValues: toForm(profile.sections[section]) as DefaultValues<F>,
    mode: "onSubmit",
    reValidateMode: "onChange",
  });
  // The revision this form's data was read at: a save based on it never overwrites a newer one.
  const base = useRef(profile.revision);
  const [formError, setFormError] = useState<string>();
  const [conflict, setConflict] = useState<Conflict>();
  const label = SECTION_LABELS[section];

  const { isDirty } = form.formState;
  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);
  useEffect(
    () => () => {
      onDirtyChange(false);
    },
    [onDirtyChange],
  );

  const mutation = useMutation({
    mutationFn: (values: F) =>
      saveSection(section, base.current, fromForm(values)),
    onMutate: () => {
      setFormError(undefined);
    },
    onSuccess: (saved) => {
      client.setQueryData(profileKey, saved);
      base.current = saved.revision;
      form.reset(toForm(saved.sections[section]));
      toast.success(`${label} saved.`);
    },
    onError: (error, values) => {
      const stale = conflictOf(error);
      if (stale) {
        setConflict(stale);
        return;
      }
      const detail = validationDetail(error);
      if (detail) {
        const field = serverFieldError(detail, values, rules);
        if (field) {
          form.setError(
            field.name as Path<F>,
            { type: "server", message: field.message },
            { shouldFocus: true },
          );
          return;
        }
        setFormError(`The server refused this section: ${detail}`);
        return;
      }
      setFormError(`${label} was not saved. ${describeError(error)}`);
    },
  });

  const onSubmit = form.handleSubmit((values) => {
    mutation.mutate(values);
  });

  const applyConflict = (current: Conflict) => {
    client.setQueryData<Profile>(profileKey, (old) =>
      old
        ? { ...old, revision: current.revision, sections: current.sections }
        : old,
    );
    base.current = current.revision;
  };

  const keepEditing = () => {
    if (conflict) applyConflict(conflict);
    setConflict(undefined);
  };
  const reload = () => {
    if (conflict) {
      applyConflict(conflict);
      form.reset(toForm(conflict.sections[section]));
      void client.invalidateQueries({ queryKey: profileKey });
      toast.info(`${label} reloaded.`);
    }
    setConflict(undefined);
  };

  return {
    form,
    onSubmit,
    saving: mutation.isPending,
    formError,
    conflict: conflict !== undefined,
    keepEditing,
    reload,
  };
}
