import { zodResolver } from "@hookform/resolvers/zod";
import { useState, type ReactNode } from "react";
import { useForm, type UseFormReturn } from "react-hook-form";

import { Form } from "@/components/ui/form";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { useJob } from "@/features/jobs/api/use-job";
import { useJobEditor, type Save } from "@/features/jobs/api/use-job-editor";
import { useSweepRunner } from "@/features/jobs/api/use-sweep-runner";
import {
  EMPTY_FILTERS,
  type FilterProps,
  type JobFilters,
} from "@/features/jobs/helpers/filters";
import {
  useFindJobs,
  type FindJobs,
} from "@/features/jobs/hooks/use-find-jobs";
import type { Search } from "@/features/profile/api/profile-api";
import {
  searchSchema,
  toSearchForm,
  type SearchForm,
} from "@/features/profile/helpers/editor-forms";

/**
 * A posting as its editors get it on its page: read from the fake api (`fakeEditableJob`) and
 * saved through the real editor, so a story shows the saving, the toasts and the rollbacks.
 */
export function EditableJob({
  id,
  children,
}: {
  id: string;
  children: (job: JobDetail, save: Save) => ReactNode;
}) {
  const job = useJob(id);
  const save = useJobEditor(id);
  return job.data ? children(job.data, save) : null;
}

/** The Find jobs of the Jobs page: one search runner, and the setup its buttons open. */
export function WithFinder({
  children,
}: {
  children: (finder: FindJobs) => ReactNode;
}) {
  const runner = useSweepRunner();
  const finder = useFindJobs(runner);
  return children(finder);
}

/** The filters the address holds on the Jobs page, here in state so every control works. */
export function WithFilters({
  initial = EMPTY_FILTERS,
  children,
}: {
  initial?: JobFilters;
  children: (props: FilterProps) => ReactNode;
}) {
  const [filters, setFilters] = useState(initial);
  return children({ filters, onChange: setFilters });
}

/** The form of Find jobs' second step over `search`, as its setup holds it. */
export function WithSearchForm({
  search,
  children,
}: {
  search: Search;
  children: (form: UseFormReturn<SearchForm>) => ReactNode;
}) {
  const form = useForm<SearchForm>({
    resolver: zodResolver(searchSchema),
    defaultValues: toSearchForm(search),
  });
  return <Form {...form}>{children(form)}</Form>;
}
