/**
 * The company boards of the Search tab: a table (company, ATS, org, enabled switch, extra exclude
 * terms, delete) that can be searched and sorted by company, and an "Add company" row whose Check
 * button asks the api whether the board exists (POST /api/profile/boards/check) before it can be
 * added. Sorting and the search box only change what is shown: the saved order stays the order
 * boards were added in. Below the sm breakpoint each row stacks into a card.
 */
import { useMutation } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  Plus,
  Search as SearchIcon,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  get,
  useFieldArray,
  useWatch,
  type UseFormReturn,
} from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { describeError, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ATS, checkBoard, type AtsName, type BoardCheck } from "./api";
import {
  BOARDS_MAX,
  boardKey,
  orgProblem,
  type SearchForm,
} from "./editor-forms";
import { Group } from "./section-ui";
import { TermChips } from "./term-chips";

const ATS_LABELS: Record<AtsName, string> = {
  ashby: "Ashby",
  greenhouse: "Greenhouse",
  lever: "Lever",
};

type Sort = "none" | "asc" | "desc";

export function BoardsTable({ form }: { form: UseFormReturn<SearchForm> }) {
  const boards = useFieldArray({ control: form.control, name: "boards" });
  const values = useWatch({ control: form.control, name: "boards" });
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("none");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  // Indices into the form array, filtered by the search box and sorted by company.
  const shown = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("en");
    const indices = values
      .map((board, index) => ({ board, index }))
      .filter(
        ({ board }) =>
          needle === "" ||
          [board.company, board.org, ATS_LABELS[board.ats]].some((field) =>
            field.toLocaleLowerCase("en").includes(needle),
          ),
      );
    if (sort !== "none") {
      indices.sort((a, b) => {
        const order = a.board.company.localeCompare(b.board.company, "en", {
          sensitivity: "base",
        });
        return sort === "asc" ? order : -order;
      });
    }
    return indices.map(({ index }) => index);
  }, [values, query, sort]);

  const enabled = values.filter((b) => b.enabled).length;
  const errors = form.formState.errors;
  const SortIcon =
    sort === "asc" ? ArrowUp : sort === "desc" ? ArrowDown : ArrowUpDown;

  return (
    <Group
      title="Company boards"
      description="Career pages swept for new postings, read from the ATS public API. Check a new one before adding it."
    >
      {boards.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No board yet.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="relative w-full sm:max-w-64">
              <SearchIcon
                aria-hidden="true"
                className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground"
              />
              <Input
                type="search"
                aria-label="Search boards"
                placeholder="Search boards"
                className="pl-8"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                }}
                onKeyDown={(event) => {
                  // Enter filters, it never submits the form.
                  if (event.key === "Enter") event.preventDefault();
                }}
              />
            </div>
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {query.trim() !== ""
                ? `${shown.length} of ${values.length} boards shown`
                : `${values.length} boards, ${enabled} enabled`}
            </p>
          </div>
          <table
            aria-label="Boards"
            className="w-full border-collapse text-sm max-sm:block"
          >
            <thead className="max-sm:sr-only">
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th
                  scope="col"
                  aria-sort={
                    sort === "asc"
                      ? "ascending"
                      : sort === "desc"
                        ? "descending"
                        : "none"
                  }
                  className="py-2 pr-2 font-medium"
                >
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-sm hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    onClick={() => {
                      setSort(
                        sort === "none"
                          ? "asc"
                          : sort === "asc"
                            ? "desc"
                            : "none",
                      );
                    }}
                  >
                    Company
                    <SortIcon aria-hidden="true" className="size-3.5" />
                    <span className="sr-only">
                      {sort === "none"
                        ? ", sort A to Z"
                        : sort === "asc"
                          ? ", sorted A to Z, sort Z to A"
                          : ", sorted Z to A, stop sorting"}
                    </span>
                  </button>
                </th>
                <th scope="col" className="py-2 pr-2 font-medium">
                  ATS
                </th>
                <th scope="col" className="py-2 pr-2 font-medium">
                  Org
                </th>
                <th scope="col" className="py-2 pr-2 font-medium">
                  Enabled
                </th>
                <th scope="col" className="py-2 pr-2 font-medium">
                  Exclude
                </th>
                <th scope="col" className="py-2 font-medium">
                  <span className="sr-only">Delete</span>
                </th>
              </tr>
            </thead>
            <tbody className="max-sm:flex max-sm:flex-col max-sm:gap-3">
              {shown.length === 0 ? (
                <tr className="max-sm:block">
                  <td
                    colSpan={6}
                    className="py-3 text-muted-foreground max-sm:block"
                  >
                    No board matches “{query.trim()}”.
                  </td>
                </tr>
              ) : null}
              {shown.map((index) => {
                const field = boards.fields[index];
                if (!field) return null;
                const open =
                  expanded.has(field.id) ||
                  get(errors, `boards.${index}.extra_exclude`) !== undefined;
                return (
                  <BoardRow
                    key={field.id}
                    form={form}
                    index={index}
                    open={open}
                    onToggle={() => {
                      setExpanded((old) => {
                        const next = new Set(old);
                        if (next.has(field.id)) next.delete(field.id);
                        else next.add(field.id);
                        return next;
                      });
                    }}
                    onRemove={() => {
                      boards.remove(index);
                    }}
                  />
                );
              })}
            </tbody>
          </table>
        </>
      )}
      <AddBoard
        form={form}
        full={boards.fields.length >= BOARDS_MAX}
        onAdd={(board) => {
          boards.append(board);
          setQuery("");
        }}
      />
    </Group>
  );
}

function BoardRow({
  form,
  index,
  open,
  onToggle,
  onRemove,
}: {
  form: UseFormReturn<SearchForm>;
  index: number;
  open: boolean;
  onToggle: () => void;
  onRemove: () => void;
}) {
  const n = index + 1;
  const { control } = form;
  const company = useWatch({ control, name: `boards.${index}.company` });
  const excluded = useWatch({
    control,
    name: `boards.${index}.extra_exclude.terms`,
  });
  const who = company.trim() || `board ${n}`;
  const cell = "py-2 pr-2 align-top max-sm:block max-sm:p-0";
  return (
    <>
      <tr
        className={cn(
          "border-b max-sm:grid max-sm:grid-cols-2 max-sm:gap-2 max-sm:rounded-md max-sm:border max-sm:p-3",
          open && "border-b-0",
        )}
      >
        <td className={cn(cell, "max-sm:col-span-2")}>
          <FormField
            control={control}
            name={`boards.${index}.company`}
            render={({ field }) => (
              <FormItem className="gap-1">
                <FormControl>
                  <Input
                    {...field}
                    aria-label={`Company of board ${n}`}
                    className="h-8 min-w-32"
                  />
                </FormControl>
                <FormMessage className="text-xs" />
              </FormItem>
            )}
          />
        </td>
        <td className={cell}>
          <FormField
            control={control}
            name={`boards.${index}.ats`}
            render={({ field }) => (
              <FormItem className="gap-1">
                <FormControl>
                  <NativeSelect
                    {...field}
                    aria-label={`ATS of board ${n}`}
                    className="h-8"
                  >
                    {ATS.map((ats) => (
                      <option key={ats} value={ats}>
                        {ATS_LABELS[ats]}
                      </option>
                    ))}
                  </NativeSelect>
                </FormControl>
                <FormMessage className="text-xs" />
              </FormItem>
            )}
          />
        </td>
        <td className={cell}>
          <FormField
            control={control}
            name={`boards.${index}.org`}
            render={({ field }) => (
              <FormItem className="gap-1">
                <FormControl>
                  <Input
                    {...field}
                    aria-label={`Org of board ${n}`}
                    spellCheck={false}
                    autoCapitalize="off"
                    autoComplete="off"
                    className="h-8 min-w-28 font-mono text-xs"
                  />
                </FormControl>
                <FormMessage className="text-xs" />
              </FormItem>
            )}
          />
        </td>
        <td className={cn(cell, "max-sm:flex max-sm:items-center")}>
          <FormField
            control={control}
            name={`boards.${index}.enabled`}
            render={({ field }) => (
              <label className="flex h-8 items-center gap-2">
                <input
                  type="checkbox"
                  role="switch"
                  aria-checked={field.value}
                  aria-label={`Sweep ${who}`}
                  className="size-4 accent-primary"
                  name={field.name}
                  ref={field.ref}
                  checked={field.value}
                  onBlur={field.onBlur}
                  onChange={(event) => {
                    field.onChange(event.target.checked);
                  }}
                />
                <span
                  aria-hidden="true"
                  className="text-xs text-muted-foreground sm:sr-only"
                >
                  {field.value ? "On" : "Off"}
                </span>
              </label>
            )}
          />
        </td>
        <td className={cn(cell, "max-sm:flex max-sm:items-center")}>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-expanded={open}
            aria-label={`Exclude terms of ${who}`}
            className="h-8 px-2 text-xs"
            onClick={onToggle}
          >
            <span
              aria-hidden="true"
              className="text-muted-foreground sm:hidden"
            >
              Exclude:
            </span>
            {excluded.length === 0
              ? "None"
              : `${excluded.length} term${excluded.length === 1 ? "" : "s"}`}
          </Button>
        </td>
        <td className={cn(cell, "pr-0 max-sm:flex max-sm:justify-end")}>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete ${who}`}
            onClick={onRemove}
          >
            <Trash2 aria-hidden="true" />
          </Button>
        </td>
      </tr>
      {open ? (
        <tr className="border-b max-sm:-mt-3 max-sm:block max-sm:rounded-b-md max-sm:border max-sm:border-t-0">
          <td colSpan={6} className="pb-3 max-sm:block max-sm:p-3">
            <TermChips
              form={form}
              name={`boards.${index}.extra_exclude`}
              label={`Exclude terms of ${who}`}
              title={`Titles to exclude at ${who}`}
              description="On top of the titles excluded for every board."
              compact
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

// --- add a company -----------------------------------------------------------------------------

type Checked = { ats: AtsName; org: string; result: BoardCheck };

function checkMessage(ats: AtsName, org: string, result: BoardCheck): string {
  const name = ATS_LABELS[ats];
  if (result.exists) {
    const postings = result.postings ?? 0;
    const count = `${postings} open posting${postings === 1 ? "" : "s"}`;
    return result.company_name
      ? `Found on ${name}: ${result.company_name}, ${count}.`
      : `Found on ${name}: ${count}.`;
  }
  switch (result.error) {
    case "not_found":
      return `No board named “${org}” on ${name}. Check the org as it appears in the careers page address.`;
    case "timeout":
      return `${name} did not answer in time. Try again, or add the board without a check.`;
    case "blocked":
      return `${name} refused the check from the server. Add the board without a check if you are sure of the org.`;
    default:
      return `${name} answered with an error. Try again later, or add the board without a check.`;
  }
}

function AddBoard({
  form,
  full,
  onAdd,
}: {
  form: UseFormReturn<SearchForm>;
  full: boolean;
  onAdd: (board: SearchForm["boards"][number]) => void;
}) {
  const [company, setCompany] = useState("");
  const [ats, setAts] = useState<AtsName>("ashby");
  const [org, setOrg] = useState("");
  const [problem, setProblem] = useState<string>();
  const [checked, setChecked] = useState<Checked>();
  const [added, setAdded] = useState<string>();

  const check = useMutation({
    mutationFn: (target: { ats: AtsName; org: string }) =>
      checkBoard(target.ats, target.org),
    onSuccess: (result, target) => {
      setChecked({ ...target, result });
      if (result.exists && result.company_name && company.trim() === "") {
        setCompany(result.company_name);
      }
    },
    onError: (error) => {
      setChecked(undefined);
      if (isApiError(error) && error.status === 429) {
        const wait = error.retryAfter;
        setProblem(
          wait
            ? `Too many checks. Try again in ${wait} seconds.`
            : "Too many checks. Try again in a minute.",
        );
        return;
      }
      setProblem(`The check failed. ${describeError(error)}`);
    },
  });

  const trimmed = org.trim();
  const current =
    checked && checked.ats === ats && checked.org === trimmed
      ? checked
      : undefined;
  const duplicate = () =>
    form
      .getValues("boards")
      .some((b) => boardKey(b) === boardKey({ ats, org: trimmed }));

  const runCheck = () => {
    setAdded(undefined);
    const found = orgProblem(trimmed);
    if (found) {
      setProblem(found);
      return;
    }
    if (duplicate()) {
      setProblem("This board is already in your list.");
      return;
    }
    setProblem(undefined);
    check.mutate({ ats, org: trimmed });
  };

  const add = () => {
    const name = company.trim();
    if (name === "") {
      setProblem("Enter the company name.");
      return;
    }
    if (duplicate()) {
      setProblem("This board is already in your list.");
      return;
    }
    onAdd({
      company: name,
      ats,
      org: trimmed,
      enabled: true,
      extra_exclude: { advanced_regex: false, terms: [] },
    });
    setAdded(`${name} added. Save search to keep it.`);
    setCompany("");
    setOrg("");
    setChecked(undefined);
    setProblem(undefined);
  };

  const canAdd = current !== undefined && current.result.error !== "not_found";
  const verified = current?.result.exists === true;

  return (
    <section
      aria-labelledby="add-board-title"
      className="flex flex-col gap-3 rounded-md border border-dashed p-3"
    >
      <h4 id="add-board-title" className="text-sm font-medium">
        Add company
      </h4>
      <div className="grid gap-2 sm:grid-cols-[1fr_8rem_1fr_auto]">
        <Input
          aria-label="New board company"
          placeholder="Company"
          value={company}
          disabled={full}
          onChange={(event) => {
            setCompany(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              runCheck();
            }
          }}
        />
        <NativeSelect
          aria-label="New board ATS"
          value={ats}
          disabled={full}
          onChange={(event) => {
            setAts(event.target.value as AtsName);
          }}
        >
          {ATS.map((value) => (
            <option key={value} value={value}>
              {ATS_LABELS[value]}
            </option>
          ))}
        </NativeSelect>
        <Input
          aria-label="New board org"
          aria-invalid={problem ? true : undefined}
          placeholder="org, as in the board address"
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
          className="font-mono text-xs"
          value={org}
          disabled={full}
          onChange={(event) => {
            setOrg(event.target.value);
            setProblem(undefined);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              runCheck();
            }
          }}
        />
        <Button
          type="button"
          variant="outline"
          disabled={full || check.isPending || trimmed === ""}
          aria-busy={check.isPending}
          onClick={runCheck}
        >
          {check.isPending ? (
            <LoaderCircle aria-hidden="true" className="animate-spin" />
          ) : (
            <SearchIcon aria-hidden="true" />
          )}
          {check.isPending ? "Checking" : "Check"}
        </Button>
      </div>
      <div role="status" aria-live="polite" className="text-sm">
        {full ? (
          <p className="text-muted-foreground">
            You have the maximum of {BOARDS_MAX} boards.
          </p>
        ) : problem ? (
          <p className="flex items-start gap-1.5 text-destructive">
            <CircleAlert
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0"
            />
            {problem}
          </p>
        ) : current ? (
          <p
            className={cn(
              "flex items-start gap-1.5",
              verified
                ? "text-emerald-700 dark:text-emerald-400"
                : "text-amber-700 dark:text-amber-400",
            )}
          >
            {verified ? (
              <CircleCheck
                aria-hidden="true"
                className="mt-0.5 size-4 shrink-0"
              />
            ) : (
              <CircleAlert
                aria-hidden="true"
                className="mt-0.5 size-4 shrink-0"
              />
            )}
            {checkMessage(current.ats, current.org, current.result)}
          </p>
        ) : added ? (
          <p className="text-muted-foreground">{added}</p>
        ) : (
          <p className="text-muted-foreground">
            The org is the last part of the board address, such as mistral.ai in
            jobs.ashbyhq.com/mistral.ai.
          </p>
        )}
      </div>
      {canAdd ? (
        <div>
          <Button
            type="button"
            variant={verified ? "default" : "outline"}
            onClick={add}
          >
            <Plus aria-hidden="true" />
            {verified ? "Add board" : "Add without a check"}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
