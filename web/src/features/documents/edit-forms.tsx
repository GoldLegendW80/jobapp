import { Plus } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { Profile } from "@/features/profile/api";
import { slugify } from "@/features/profile/forms";

import {
  MAX,
  listAt,
  span,
  type EmployerFact,
  type Path,
} from "./edit-content";
import { EntryButtons, ListField, TextField } from "./edit-fields";
import { inputId, useListMoves, type Form } from "./edit-lists";

const text = (value: unknown): string =>
  typeof value === "string" ? value : "";

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-4 rounded-lg border p-3 sm:p-4"
    >
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

/** The fixed facts, read only: the template prints them from the profile, never from the form. */
export function ProfileFacts({
  profile,
  letter,
}: {
  profile: Profile | undefined;
  letter: boolean;
}) {
  if (profile === undefined) return null;
  const identity = profile.sections.identity;
  const { employers, education } = profile.sections.experience;
  const contact = [
    identity.email,
    identity.phone,
    identity.city,
    identity.website,
  ].filter((one): one is string => typeof one === "string" && one !== "");
  return (
    <Section title="From your profile, printed as they are">
      <dl className="grid gap-2 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="text-muted-foreground">Name</dt>
        <dd>{identity.full_name ?? "Not in your profile yet"}</dd>
        <dt className="text-muted-foreground">Contact lines</dt>
        <dd>{contact.length === 0 ? "None yet" : contact.join(", ")}</dd>
        {letter ? null : (
          <>
            <dt className="text-muted-foreground">Employers</dt>
            <dd>
              {employers.length === 0
                ? "None yet"
                : employers
                    .map((one) =>
                      [one.organisation, one.title, span(one.start, one.end)]
                        .filter((part) => part !== "")
                        .join(", "),
                    )
                    .join("; ")}
            </dd>
            <dt className="text-muted-foreground">Schools</dt>
            <dd>
              {education.length === 0
                ? "None yet"
                : education.map((one) => one.school).join("; ")}
            </dd>
            <dt className="text-muted-foreground">Languages</dt>
            <dd>
              {identity.languages.length === 0
                ? "None yet"
                : identity.languages
                    .map((one) =>
                      one.level ? `${one.name} (${one.level})` : one.name,
                    )
                    .join(", ")}
            </dd>
          </>
        )}
      </dl>
      <p className="text-sm">
        <Link className="underline" to="/profile?tab=identity">
          Change your name and contact in your profile
        </Link>
        {letter ? null : (
          <>
            {", or "}
            <Link className="underline" to="/profile?tab=experience">
              your employers and schools
            </Link>
          </>
        )}
        .
      </p>
    </Section>
  );
}

function Experience({
  form,
  employers,
  known,
}: {
  form: Form;
  employers: Map<string, EmployerFact>;
  /** The profile (and the base CV) are read: an employer missing from them is said. */
  known: boolean;
}) {
  const path = ["experience"] as const;
  const entries = listAt(form.draft, path);
  const keyOf = (index: number) =>
    text(
      (entries[index] as { employer_key?: unknown } | undefined)?.employer_key,
    );
  const used = new Set(entries.map((_, index) => keyOf(index)));
  const free = [...employers.values()].filter((one) => !used.has(one.key));
  const [adding, setAdding] = useState("");
  const chosen = free.some((one) => one.key === adding)
    ? adding
    : (free[0]?.key ?? "");
  const moves = useListMoves(form, path, "Employer");
  const base = inputId(form, path);
  const selectId = `${base}-choice`;
  return (
    <Section title="Experience">
      <p className="text-sm text-muted-foreground">
        Each employer&apos;s name, title and dates come from your profile; you
        write the bullets.
      </p>
      {entries.map((_, index) => {
        const key = keyOf(index);
        const fact = employers.get(key);
        const name = fact?.organisation ?? key;
        return (
          <fieldset
            key={index}
            className="flex min-w-0 flex-col gap-2 border-t pt-3"
          >
            <legend className="text-sm font-medium">{name}</legend>
            <div className="flex items-start justify-between gap-2">
              {fact === undefined && known ? (
                <p
                  role="note"
                  className="text-sm text-amber-700 dark:text-amber-400"
                >
                  This employer is no longer in your profile, nor in the base
                  CV, so the page has no name, title or dates to print above
                  these bullets. Add it back in your profile, or remove it here:
                  nothing is changed for you.
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {[
                    fact?.title ?? "",
                    span(fact?.start ?? null, fact?.end ?? null),
                  ]
                    .filter((part) => part !== "")
                    .join(", ")}
                </p>
              )}
              <EntryButtons
                id={`${base}-${String(index)}`}
                what={`employer ${name}`}
                index={index}
                count={entries.length}
                onMove={(to, button) => {
                  moves.move(index, to, button);
                }}
                onRemove={() => {
                  moves.remove(index);
                }}
              />
            </div>
            <ListField
              form={form}
              path={[...path, index, "bullets"]}
              legend="Bullets"
              noun="Bullet"
              context={name}
              max={MAX.bullets}
              itemMax={MAX.bullet}
              multiline
            />
          </fieldset>
        );
      })}
      <div className="flex flex-col gap-1.5 border-t pt-3">
        <Label htmlFor={selectId}>Add an employer of your profile</Label>
        <div className="flex flex-wrap gap-2">
          <div className="min-w-0 flex-1">
            <NativeSelect
              id={selectId}
              value={chosen}
              disabled={free.length === 0}
              onChange={(event) => {
                setAdding(event.target.value);
              }}
            >
              {free.length === 0 ? (
                <option value="">Every employer is in this CV</option>
              ) : (
                free.map((one) => (
                  <option key={one.key} value={one.key}>
                    {one.organisation}
                  </option>
                ))
              )}
            </NativeSelect>
          </div>
          <Button
            id={`${base}-add`}
            type="button"
            variant="outline"
            disabled={chosen === "" || entries.length >= MAX.employers}
            onClick={() => {
              form.set(path, [
                ...entries,
                { employer_key: chosen, bullets: [] },
              ]);
            }}
          >
            <Plus aria-hidden="true" />
            Add
          </Button>
        </div>
      </div>
      <p aria-live="polite" className="sr-only">
        {moves.said}
      </p>
    </Section>
  );
}

/** A list of terms typed as one line; the line keeps what is typed until the list changes elsewhere. */
function TermsField({ form, path }: { form: Form; path: Path }) {
  const items = listAt(form.draft, path).map(text);
  const key = JSON.stringify(items);
  const split = (line: string) => line.split(",").map((one) => one.trim());
  const [typed, setTyped] = useState({ line: items.join(", "), key });
  if (typed.key !== key) {
    const kept = (list: string[]) =>
      JSON.stringify(list.map((one) => one.trim()).filter((one) => one !== ""));
    const same = kept(split(typed.line)) === kept(items);
    setTyped({ line: same ? typed.line : items.join(", "), key });
  }
  return (
    <TextField
      form={form}
      path={path}
      label="Terms, separated by commas"
      max={MAX.skillItems * (MAX.term + 2)}
      value={typed.line}
      onText={(line) => {
        const next = split(line);
        setTyped({ line, key: JSON.stringify(next) });
        form.set(path, next);
      }}
    />
  );
}

function Skills({ form }: { form: Form }) {
  const path = ["skills"] as const;
  const groups = listAt(form.draft, path);
  const moves = useListMoves(form, path, "Group");
  const base = inputId(form, path);
  return (
    <Section title="Skills">
      {groups.map((_, index) => (
        <fieldset
          key={index}
          className="flex min-w-0 flex-col gap-2 border-t pt-3"
        >
          <legend className="text-sm font-medium">Group {index + 1}</legend>
          <div className="flex justify-end">
            <EntryButtons
              id={`${base}-${String(index)}`}
              what={`group ${String(index + 1)}`}
              index={index}
              count={groups.length}
              onMove={(to, button) => {
                moves.move(index, to, button);
              }}
              onRemove={() => {
                moves.remove(index);
              }}
            />
          </div>
          <TextField
            form={form}
            path={[...path, index, "label"]}
            label="Label"
            max={MAX.term}
          />
          <TermsField form={form} path={[...path, index, "items"]} />
        </fieldset>
      ))}
      <div>
        <Button
          id={`${base}-add`}
          type="button"
          variant="outline"
          size="sm"
          disabled={groups.length >= MAX.skills}
          onClick={() => {
            form.set(path, [...groups, { label: "", items: [] }]);
            moves.focus(`${base}-${String(groups.length)}-label`);
          }}
        >
          <Plus aria-hidden="true" />
          Add a group
        </Button>
      </div>
      <p aria-live="polite" className="sr-only">
        {moves.said}
      </p>
    </Section>
  );
}

interface Column {
  name: string;
  label: string;
  period?: boolean;
}

const FIXED_LISTS: {
  name: "employers" | "schools" | "languages";
  title: string;
  noun: string;
  max: number;
  columns: Column[];
}[] = [
  {
    name: "employers",
    title: "Employers",
    noun: "Employer",
    max: MAX.fixedEmployers,
    columns: [
      { name: "organisation", label: "Organisation" },
      { name: "title", label: "Title" },
      { name: "start", label: "Start", period: true },
      { name: "end", label: "End, empty for today", period: true },
      { name: "location", label: "Location" },
    ],
  },
  {
    name: "schools",
    title: "Schools",
    noun: "School",
    max: MAX.fixedSchools,
    columns: [
      { name: "school", label: "School" },
      { name: "degree", label: "Degree" },
      { name: "start", label: "Start", period: true },
      { name: "end", label: "End", period: true },
      { name: "location", label: "Location" },
    ],
  },
  {
    name: "languages",
    title: "Languages",
    noun: "Language",
    max: MAX.fixedLanguages,
    columns: [
      { name: "name", label: "Language" },
      { name: "level", label: "Level" },
    ],
  },
];

/** A list of the base CV's `fixed` block: one small form per entry. */
function FixedList({
  form,
  list,
}: {
  form: Form;
  list: (typeof FIXED_LISTS)[number];
}) {
  const path = ["fixed", list.name] as const;
  const rows = listAt(form.draft, path);
  const moves = useListMoves(form, path, list.noun);
  const base = inputId(form, path);
  const lower = list.noun.toLowerCase();
  // An employer's key is what the CV's experience names: made from the organisation until used.
  const used = new Set(
    listAt(form.draft, ["experience"]).map((one) =>
      text((one as { employer_key?: unknown }).employer_key),
    ),
  );
  const setOrganisation = (index: number, value: string) => {
    const row = rows[index] as Record<string, unknown>;
    const key = text(row.key);
    const taken = new Set(
      rows
        .filter((_, i) => i !== index)
        .map((one) => text((one as { key?: unknown }).key)),
    );
    let next = key;
    if (key === "" || !used.has(key)) {
      const stem = slugify(value) || "employer";
      next = stem;
      for (let n = 2; taken.has(next); n += 1) next = `${stem}-${String(n)}`;
    }
    form.set([...path, index], { ...row, organisation: value, key: next });
  };
  return (
    <fieldset className="flex min-w-0 flex-col gap-3">
      <legend className="text-sm font-medium">{list.title}</legend>
      {rows.map((_, index) => (
        <div key={index} className="flex flex-col gap-2 border-t pt-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">
              {list.noun} {index + 1}
            </span>
            <EntryButtons
              id={`${base}-${String(index)}`}
              what={`${lower} ${String(index + 1)}`}
              index={index}
              count={rows.length}
              onMove={(to, button) => {
                moves.move(index, to, button);
              }}
              onRemove={() => {
                moves.remove(index);
              }}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {list.columns.map((column) => {
              const at = [...path, index, column.name];
              return (
                <TextField
                  key={column.name}
                  form={form}
                  path={at}
                  label={`${column.label}, ${lower} ${String(index + 1)}`}
                  max={column.period ? 7 : MAX.line}
                  inputMode={column.period ? "numeric" : undefined}
                  hint={
                    column.period
                      ? "A year or a month: 2021 or 2021-03."
                      : undefined
                  }
                  onText={
                    column.name === "organisation"
                      ? (typed) => {
                          setOrganisation(index, typed);
                        }
                      : undefined
                  }
                />
              );
            })}
          </div>
        </div>
      ))}
      <div>
        <Button
          id={`${base}-add`}
          type="button"
          variant="outline"
          size="sm"
          disabled={rows.length >= list.max}
          onClick={() => {
            const empty = Object.fromEntries(
              list.columns.map((column) => [
                column.name,
                column.period ? null : "",
              ]),
            );
            form.set(path, [
              ...rows,
              list.name === "employers" ? { key: "", ...empty } : empty,
            ]);
            moves.focus(
              `${base}-${String(rows.length)}-${list.columns[0]?.name ?? ""}`,
            );
          }}
        >
          <Plus aria-hidden="true" />
          Add {/^[aeiou]/.test(lower) ? "an" : "a"} {lower}
        </Button>
      </div>
      <p aria-live="polite" className="sr-only">
        {moves.said}
      </p>
    </fieldset>
  );
}

/** The base CV's own fixed facts: used where the profile is still empty. */
function FixedBlock({ form }: { form: Form }) {
  return (
    <Section title="Facts of this base CV">
      <p className="text-sm text-muted-foreground">
        Used only where your profile is still empty: your profile always comes
        first.
      </p>
      {FIXED_LISTS.map((list) => (
        <FixedList key={list.name} form={form} list={list} />
      ))}
    </Section>
  );
}

/** A line of the form: the field, its label, its hard limit, one line or several. */
type Line = [field: string, label: string, max: number, multiline?: boolean];

function Lines({ form, lines }: { form: Form; lines: Line[] }) {
  return lines.map(([field, label, max, multiline]) => (
    <TextField
      key={field}
      form={form}
      path={[field]}
      label={label}
      max={max}
      multiline={multiline}
    />
  ));
}

const CV_TOP: Line[] = [
  ["target", "What this CV is for (not printed)", MAX.line],
  ["headline", "Headline", MAX.headline],
  ["summary", "Summary", MAX.summary, true],
];

export function CvForm({
  form,
  base,
  employers,
  known,
}: {
  form: Form;
  base: boolean;
  employers: Map<string, EmployerFact>;
  known: boolean;
}) {
  return (
    <>
      <Section title="Top of the page">
        <Lines form={form} lines={CV_TOP} />
        <ListField
          form={form}
          path={["highlights"]}
          legend="Highlights"
          noun="Highlight"
          max={MAX.highlights}
          itemMax={MAX.bullet}
        />
        <ListField
          form={form}
          path={["competencies"]}
          legend="Competencies"
          noun="Competency"
          max={MAX.competencies}
          itemMax={MAX.term}
        />
      </Section>
      <Experience form={form} employers={employers} known={known} />
      <Skills form={form} />
      {base ? <FixedBlock form={form} /> : null}
      <Section title="For you">
        <Lines
          form={form}
          lines={[["notes", "Notes (not printed)", MAX.notes, true]]}
        />
      </Section>
    </>
  );
}

export function LetterForm({ form }: { form: Form }) {
  return (
    <Section title="The letter">
      <Lines
        form={form}
        lines={[
          ["target", "What this letter is for (not printed)", MAX.line],
          ["headline", "Headline", MAX.headline],
          ["recipient", "Recipient", MAX.line],
          ["subject", "Subject", MAX.line],
          ["salutation", "Salutation", MAX.line],
        ]}
      />
      <ListField
        form={form}
        path={["paragraphs"]}
        legend="Paragraphs"
        noun="Paragraph"
        max={MAX.paragraphs}
        itemMax={MAX.paragraph}
        multiline
      />
      <Lines form={form} lines={[["closing", "Closing", MAX.line]]} />
    </Section>
  );
}
