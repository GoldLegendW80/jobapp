/**
 * Facts, Skills and Search, read only in this release: their editors come next (task P5), but
 * the imported data is already visible. Fact bodies render through the safe Markdown renderer.
 */
import { ChevronRight, Sparkles } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";

import type { Category, Facts, Search, Skills, TermList } from "./api";
import { Group } from "./section-ui";
import { Markdown } from "./markdown";

function ComingNext({ what }: { what: string }) {
  return (
    <p className="flex items-start gap-2 rounded-md border border-dashed p-3 text-sm text-muted-foreground">
      <Sparkles aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>
        <strong className="font-medium text-foreground">Coming next:</strong>{" "}
        editing {what} in the browser. Here is what your profile holds today.
      </span>
    </p>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

function Terms({ list, label }: { list: TermList; label: string }) {
  if (list.terms.length === 0) return <Empty>None.</Empty>;
  return (
    <div className="flex flex-col gap-2">
      {list.advanced_regex ? (
        <p className="text-xs text-muted-foreground">Regular expressions</p>
      ) : null}
      <ul aria-label={label} className="flex flex-wrap gap-1.5">
        {list.terms.map((term) => (
          <li key={term} className="max-w-full">
            <Badge
              variant="secondary"
              className="max-w-full font-mono font-normal break-all whitespace-normal"
            >
              {term}
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FactsTab({ facts }: { facts: Facts }) {
  return (
    <div className="flex flex-col gap-4">
      <ComingNext what="the fact base" />
      <Group
        title="Fact base"
        description="The only source of claims for generated documents, in named blocks."
      >
        {facts.blocks.length === 0 ? (
          <Empty>No fact block yet.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {facts.blocks.map((block) => (
              <li key={block.title}>
                <details className="group rounded-md border">
                  <summary className="flex cursor-pointer list-none items-center gap-2 p-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
                    <ChevronRight
                      aria-hidden="true"
                      className="size-4 shrink-0 transition-transform group-open:rotate-90"
                    />
                    <span className="min-w-0 break-words">{block.title}</span>
                  </summary>
                  <div className="border-t p-3">
                    <Markdown source={block.body} />
                  </div>
                </details>
              </li>
            ))}
          </ul>
        )}
      </Group>
    </div>
  );
}

function Categories({ title, items }: { title: string; items: Category[] }) {
  return (
    <Group title={title}>
      {items.length === 0 ? (
        <Empty>None.</Empty>
      ) : (
        <ul className="flex flex-col gap-4">
          {items.map((category) => (
            <li key={category.name} className="flex flex-col gap-2">
              <h4 className="text-sm font-medium">{category.name}</h4>
              <Terms list={category} label={`Terms of ${category.name}`} />
            </li>
          ))}
        </ul>
      )}
    </Group>
  );
}

export function SkillsTab({ skills }: { skills: Skills }) {
  return (
    <div className="flex flex-col gap-4">
      <ComingNext what="the scoring model" />
      <Group title="Experience">
        <p className="text-sm">
          {skills.years_experience === null
            ? "Years of experience not set."
            : `${skills.years_experience.toLocaleString("en")} years of experience.`}
        </p>
      </Group>
      <Categories title="Skills you have" items={skills.have} />
      <Categories title="Adjacent skills" items={skills.adjacent} />
      <Categories title="Skills you lack" items={skills.none} />
      <Group
        title="Hard gates"
        description="Requirements that lower the fit score when a posting asks for them."
      >
        {skills.gates.length === 0 ? (
          <Empty>None.</Empty>
        ) : (
          <ul className="flex flex-col gap-4">
            {skills.gates.map((gate) => (
              <li key={gate.name} className="flex flex-col gap-2">
                <h4 className="text-sm font-medium">
                  {gate.name}{" "}
                  <span className="font-normal text-muted-foreground">
                    (penalty {gate.penalty})
                  </span>
                </h4>
                <Terms list={gate} label={`Terms of ${gate.name}`} />
              </li>
            ))}
          </ul>
        )}
      </Group>
    </div>
  );
}

const STANCE: Record<NonNullable<Search["remote_stance"]>, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
  office: "Office",
  any: "Any",
};

export function SearchTab({ search }: { search: Search }) {
  return (
    <div className="flex flex-col gap-4">
      <ComingNext what="your search" />
      <Group title="Titles to include">
        <Terms list={search.title_include} label="Titles to include" />
      </Group>
      <Group title="Titles to exclude">
        <Terms list={search.title_exclude} label="Titles to exclude" />
      </Group>
      <Group title="Locations">
        <Terms list={search.location} label="Locations" />
      </Group>
      <Group title="Preferences">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-muted-foreground">Contracts</dt>
          <dd>
            {search.contract_types.length
              ? search.contract_types.join(", ")
              : "None"}
          </dd>
          <dt className="text-muted-foreground">Remote</dt>
          <dd>
            {search.remote_stance ? STANCE[search.remote_stance] : "Not set"}
          </dd>
          <dt className="text-muted-foreground">Minimum fit</dt>
          <dd>{search.min_fit}</dd>
          <dt className="text-muted-foreground">Posted within</dt>
          <dd>{search.days} days</dd>
        </dl>
      </Group>
      <Group title="Boards" description="Company job boards the search sweeps.">
        {search.boards.length === 0 ? (
          <Empty>No board yet.</Empty>
        ) : (
          <ul
            aria-label="Boards"
            className="divide-y rounded-md border text-sm"
          >
            {search.boards.map((board) => (
              <li
                key={`${board.ats}:${board.org}`}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
              >
                <span className="min-w-0 font-medium break-words">
                  {board.company}
                </span>
                <span className="flex items-center gap-2 text-muted-foreground">
                  <span className="break-all">
                    {board.ats}, {board.org}
                  </span>
                  <Badge variant={board.enabled ? "secondary" : "outline"}>
                    {board.enabled ? "On" : "Off"}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Group>
      <Group title="Never apply">
        {search.never_apply.length === 0 ? (
          <Empty>No rule.</Empty>
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {search.never_apply.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        )}
      </Group>
    </div>
  );
}
