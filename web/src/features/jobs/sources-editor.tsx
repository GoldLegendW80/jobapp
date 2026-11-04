/**
 * "Where to search": one box for any address, a few examples, and the list (sources.ts). Shared
 * by Find jobs (search-setup.tsx) and the wizard's "Websites to search" step. Controlled by
 * `useSources` (use-sources.ts), which saves.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  Building2,
  Globe,
  LoaderCircle,
  Pause,
  Plus,
  Rss,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/advanced-section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SiteSignIn } from "@/features/browser/site-signin";
import type {
  CompanyBoard,
  LinkedInSearch,
  Search,
} from "@/features/profile/api";
import { describeError, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  ATS_NAMES,
  boardKey,
  detectBoard,
  getBoardSuggestions,
  savedBoard,
  suggestionsKey,
} from "./boards-api";
import {
  ADZUNA_KEY_PLACE,
  FEED_NAMES,
  adzunaOf,
  getJobFeeds,
  jobFeedsKey,
} from "./job-feeds";
import {
  platformOf,
  type Permission,
  type Protected,
} from "./site-permissions";
import {
  EXAMPLES,
  PLATFORM_NAMES,
  addressProblem,
  checkSite,
  feedOf,
  listed,
  resumed,
  rowsOf,
  siteOf,
  sourceCount,
  withBoard,
  withFeed,
  withScheme,
  withoutRow,
  type RowKind,
  type SourceRow,
} from "./sources";
import { useStarterBoards, type Sources } from "./use-sources";

// LinkedIn keeps one switch for its search, its reader and Easy Apply (owner, 2026-10-27), so
// its line says what else adding it allows. The server's sentence and its version stay as they are.
const ALSO_ALLOWS: Partial<Record<Protected, string>> = {
  linkedin:
    "Adding LinkedIn also allows Easy Apply with your LinkedIn account. Each application still needs your approval before it is sent.",
};

const LINKEDIN_SEARCH_LABELS: Record<LinkedInSearch, string> = {
  api: "Without signing in, through LinkedIn's public job search",
  browser: "Signed in, in your own browser",
};

const KIND_LOOK: Record<RowKind, { icon: ReactNode; tone: string }> = {
  website: {
    icon: <Globe aria-hidden="true" className="size-4" />,
    tone: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  },
  feed: {
    icon: <Rss aria-hidden="true" className="size-4" />,
    tone: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  },
  board: {
    icon: <Building2 aria-hidden="true" className="size-4" />,
    tone: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
  },
};

export function SourcesEditor({
  sources,
  reviewed,
  disabled = false,
  heading = "h3",
  step,
  showTitle = true,
}: {
  sources: Sources;
  /** The search as last saved: what saving changes is said against it. */
  reviewed: Search;
  disabled?: boolean;
  heading?: "h2" | "h3";
  /** The number shown before the title, when the list is one part of a longer form. */
  step?: number;
  /** false when the page around already names the list (the wizard's step). */
  showTitle?: boolean;
}) {
  const id = useId();
  const { draft, setDraft, pending, setPending } = sources;
  const [adding, setAdding] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const rows = rowsOf(draft);
  const count = sourceCount(draft);
  const locked = disabled || adding || sources.saving;
  const Heading = heading;

  const done = (sentence: string) => {
    setProblem(null);
    setSaid(sentence);
  };

  /** Adds what the person typed or picked; true when it is in the list now. */
  const add = async (
    raw: string,
    name?: string,
    preset?: Protected,
  ): Promise<boolean> => {
    const wrong = addressProblem(raw);
    if (wrong) {
      setProblem(wrong);
      return false;
    }
    const url = withScheme(raw);
    const feed = feedOf(url);
    if (feed) {
      if (draft.feeds.includes(feed)) {
        setProblem("This website is already in your list.");
        return false;
      }
      setDraft({ ...draft, feeds: withFeed(draft.feeds, feed) });
      done(`${FEED_NAMES[feed]} added.`);
      return true;
    }
    const asSite = () => {
      const checked = checkSite(draft.sites, siteOf(url, preset));
      if ("error" in checked) {
        setProblem(checked.error);
        return false;
      }
      setDraft({ ...draft, sites: checked.sites });
      done(`${name ?? shortName(url)} added.`);
      return true;
    };
    if (preset || platformOf(siteOf(url))) return asSite();
    // A company job board is read through its official API: the api names its ATS and id.
    setAdding(true);
    try {
      const found = await detectBoard(url);
      const next = withBoard(draft.boards ?? [], savedBoard(found));
      if ("error" in next) {
        setProblem(next.error);
        return false;
      }
      setDraft({ ...draft, boards: next.boards });
      done(`${found.company} added.`);
      return true;
    } catch (failure) {
      if (isApiError(failure) && failure.status === 422) return asSite();
      setProblem(`The address could not be checked. ${describeError(failure)}`);
      return false;
    } finally {
      setAdding(false);
    }
  };

  const addTyped = async () => {
    if (await add(pending)) setPending("");
  };

  const addBoards = (boards: readonly CompanyBoard[]) => {
    let list = draft.boards ?? [];
    let added = 0;
    for (const board of boards) {
      const next = withBoard(list, board);
      if ("error" in next) {
        setProblem(next.error);
        break;
      }
      list = next.boards;
      added += 1;
    }
    setDraft({ ...draft, boards: list });
    const [only] = boards;
    setSaid(
      added === 1 && only
        ? `${only.company} added.`
        : `${String(added)} company boards added.`,
    );
  };

  const examples = EXAMPLES.filter((example) => {
    // A known job website is offered while no address of it is in the list.
    if (example.platform)
      return !draft.sites.some((site) => platformOf(site) === example.platform);
    return (
      !listed(draft, example.url) && !boardListed(draft.boards, example.url)
    );
  });
  const withdrawn = sources.withdrawn(reviewed);

  return (
    <section
      className="space-y-4"
      aria-labelledby={showTitle ? `${id}-title` : undefined}
      aria-label={showTitle ? undefined : "Where to search"}
    >
      {showTitle ? (
        <Heading
          id={`${id}-title`}
          className="flex items-center gap-2 text-base font-semibold tracking-tight"
        >
          {step ? (
            <span
              aria-hidden="true"
              className="flex size-7 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground"
            >
              {step}
            </span>
          ) : null}
          Where to search
        </Heading>
      ) : null}

      <p className="text-sm text-muted-foreground">
        Add any website with job offers:
      </p>
      <ul className="grid gap-1.5 text-sm text-muted-foreground">
        <Kind kind="website" text="A job website, like LinkedIn or Indeed" />
        <Kind
          kind="board"
          text="A company job board, like jobs.ashbyhq.com/doctolib"
        />
        <Kind
          kind="website"
          text="Any careers page, like https://www.example.com/careers"
        />
      </ul>

      <div className="space-y-2">
        <Label htmlFor={`${id}-address`}>Website address</Label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            id={`${id}-address`}
            type="url"
            inputMode="url"
            autoComplete="url"
            maxLength={2000}
            value={pending}
            disabled={locked}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${id}-problem` : `${id}-hint`}
            onChange={(event) => {
              setPending(event.target.value);
              setProblem(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void addTyped();
              }
            }}
            placeholder="https://jobs.ashbyhq.com/doctolib"
          />
          <Button
            type="button"
            variant="outline"
            disabled={locked || !pending.trim()}
            onClick={() => void addTyped()}
          >
            {adding ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : (
              <Plus aria-hidden="true" />
            )}
            {adding ? "Checking" : "Add"}
          </Button>
        </div>
        {problem ? (
          <p
            id={`${id}-problem`}
            role="alert"
            className="text-sm text-destructive"
          >
            {problem}
          </p>
        ) : (
          <p id={`${id}-hint`} className="text-xs text-muted-foreground">
            Websites are searched by the assistant with your model key. Job
            feeds and company job boards are read directly, at no cost.
          </p>
        )}
      </div>

      {examples.length ? (
        <div className="space-y-2">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <Sparkles aria-hidden="true" className="size-4 text-amber-500" />
            Try one
          </p>
          <ul aria-label="Examples" className="flex flex-wrap gap-2">
            {examples.map((example) => (
              <li key={example.url}>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="rounded-full"
                  disabled={locked}
                  aria-label={`Add ${example.label}`}
                  onClick={() =>
                    void add(example.url, example.label, example.platform)
                  }
                >
                  <Plus aria-hidden="true" />
                  {example.label}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium" id={`${id}-list`}>
            Your list
          </p>
          <Badge variant={count ? "secondary" : "outline"}>
            {count === 1 ? "1 place" : `${String(count)} places`}
          </Badge>
        </div>
        {rows.length === 0 ? (
          <div className="flex items-center gap-3 rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
            <Sparkles aria-hidden="true" className="size-5 shrink-0" />
            <p>
              Nothing yet. Add an address above or pick an example, and the app
              searches every place on your list.
            </p>
          </div>
        ) : (
          <ul aria-labelledby={`${id}-list`} className="space-y-2">
            {rows.map((row) => (
              <SourceLine
                key={row.key}
                row={row}
                sources={sources}
                reviewed={reviewed}
                disabled={locked}
                onRemove={() => {
                  setDraft(withoutRow(draft, row));
                  done(`${row.name} removed.`);
                }}
                onResume={() => {
                  setDraft(resumed(draft, row));
                  done(`${row.name} is searched again.`);
                }}
              />
            ))}
          </ul>
        )}
        {withdrawn.length ? (
          <p className="text-xs text-muted-foreground">
            Saving takes back your permission to search{" "}
            {withdrawn
              .map((platform) => PLATFORM_NAMES[platform])
              .join(" and ")}
            .
          </p>
        ) : null}
        <p aria-live="polite" className="sr-only">
          {said}
        </p>
      </div>

      <BoardIdeas
        boards={draft.boards}
        disabled={locked}
        onAdd={(boards) => {
          addBoards(boards);
        }}
      />
    </section>
  );
}

function shortName(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
}

function boardListed(boards: readonly CompanyBoard[] | null, url: string) {
  // The examples' boards: jobs.ashbyhq.com/<id> and job-boards.greenhouse.io/<id>.
  const match =
    /^https:\/\/(jobs\.ashbyhq\.com|job-boards\.greenhouse\.io)\/([^/]+)/.exec(
      url,
    );
  if (!match || !boards) return false;
  const ats = match[1] === "jobs.ashbyhq.com" ? "ashby" : "greenhouse";
  const key = boardKey({ ats, board: match[2] ?? "" });
  return boards.some((board) => boardKey(board) === key);
}

function Kind({ kind, text }: { kind: RowKind; text: string }) {
  const look = KIND_LOOK[kind];
  return (
    <li className="flex items-center gap-2">
      <span
        aria-hidden="true"
        className={cn(
          "flex size-6 shrink-0 items-center justify-center rounded-md",
          look.tone,
        )}
      >
        {look.icon}
      </span>
      <span className="min-w-0 break-words">{text}</span>
    </li>
  );
}

function SourceLine({
  row,
  sources,
  reviewed,
  disabled,
  onRemove,
  onResume,
}: {
  row: SourceRow;
  sources: Sources;
  reviewed: Search;
  disabled: boolean;
  onRemove: () => void;
  onResume: () => void;
}) {
  const id = useId();
  const look = KIND_LOOK[row.kind];
  const isLinkedIn = row.kind === "website" && row.platform === "linkedin";
  const how = isLinkedIn
    ? sources.linkedinMode === "api"
      ? "Job website, read through LinkedIn's public job search"
      : "Job website, searched by the assistant in your browser"
    : row.how;
  // The first website of a known job website says what saving does to its permission; any other
  // address of that website says it needs the same permission.
  const site =
    row.kind === "website" ? sources.draft.sites[row.index] : undefined;
  const first =
    row.platform !== null &&
    sources.draft.sites.findIndex(
      (item) => platformOf(item) === row.platform,
    ) === row.index;
  const owned =
    site?.platform === "custom" && row.platform
      ? `This address is ${PLATFORM_NAMES[row.platform]}'s: its search needs ${PLATFORM_NAMES[row.platform]}'s permission.`
      : null;
  const note = row.platform
    ? [owned, first ? sources.noteOf(row.platform, reviewed) : null]
        .filter(Boolean)
        .join(" ") || null
    : null;
  return (
    <li
      className={cn(
        "flex min-w-0 items-start gap-3 rounded-xl border bg-card p-3 shadow-xs motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-top-1",
        !row.enabled && "opacity-75",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
          look.tone,
        )}
      >
        {look.icon}
      </span>
      <div className="min-w-0 flex-1 space-y-1 text-sm">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="min-w-0 font-medium break-all">{row.name}</span>
          {row.enabled ? null : (
            <Badge variant="outline">
              <Pause aria-hidden="true" />
              Paused
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{how}</p>
        {row.kind === "website" && row.platform ? (
          <RiskNote
            id={`${id}-risk`}
            platform={row.platform}
            state={sources.permissionOf(row.platform)}
            note={note}
          />
        ) : null}
        {isLinkedIn && first && row.enabled ? (
          <LinkedInSearchChoice
            value={sources.linkedinMode}
            onChange={sources.setLinkedinMode}
          />
        ) : null}
        {row.feed === "adzuna" ? <AdzunaNote /> : null}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {row.enabled ? null : (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={onResume}
            >
              Search it again
            </Button>
          )}
          {/* B17: sign in to this website once, then searches read it signed in. */}
          {row.kind === "website" ? <SiteSignIn url={row.url} /> : null}
        </div>
      </div>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="shrink-0"
        disabled={disabled}
        aria-label={`Remove ${row.name}`}
        onClick={onRemove}
      >
        <X aria-hidden="true" />
      </Button>
    </li>
  );
}

/** A job website's risk sentence as the server words it now, what else adding it allows, and
 * what saving does. */
function RiskNote({
  id,
  platform,
  state,
  note,
}: {
  id: string;
  platform: Protected;
  state: UseQueryResult<Permission>;
  note: string | null;
}) {
  const label = PLATFORM_NAMES[platform];
  const also = ALSO_ALLOWS[platform];
  return (
    <div
      id={id}
      className="flex gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-950 dark:bg-amber-950/40 dark:text-amber-100"
    >
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
      <div className="space-y-1">
        {state.isPending ? (
          <p>Loading the {label} risk sentence…</p>
        ) : state.isError ? (
          <p className="text-destructive">
            The {label} risk sentence could not be loaded, so its permission
            cannot be given now.
          </p>
        ) : (
          <p>{state.data.text}</p>
        )}
        {also ? <p>{also}</p> : null}
        {note && state.isSuccess ? <p className="font-medium">{note}</p> : null}
      </div>
    </div>
  );
}

/** How a LinkedIn in the list is searched (owner, 2026-10-30: "Both, switchable in setup"): its
 * public job search, without an account (the default), or the browser agent, signed in. */
function LinkedInSearchChoice({
  value,
  onChange,
}: {
  value: LinkedInSearch;
  onChange: (value: LinkedInSearch) => void;
}) {
  const name = useId();
  return (
    <fieldset className="space-y-1 pt-1 text-xs">
      <legend className="mb-1 font-medium">How LinkedIn is searched</legend>
      {(["api", "browser"] as const).map((mode) => (
        <label key={mode} className="flex min-h-6 items-center gap-2">
          <input
            type="radio"
            name={name}
            value={mode}
            className="size-3.5 shrink-0 accent-primary"
            checked={value === mode}
            onChange={() => {
              onChange(mode);
            }}
          />
          {LINKEDIN_SEARCH_LABELS[mode]}
        </label>
      ))}
      <p className="text-muted-foreground">
        Easy Apply always uses your browser.
      </p>
    </fieldset>
  );
}

function AdzunaNote() {
  const feeds = useQuery({
    queryKey: jobFeedsKey,
    queryFn: ({ signal }) => getJobFeeds(signal),
    retry: false,
  });
  const adzuna = adzunaOf(feeds.data);
  if (feeds.isPending || adzuna?.key_stored) return null;
  return (
    <p className="text-xs">
      Adzuna needs your own free key, or it is skipped.{" "}
      <Link className="underline underline-offset-2" to={ADZUNA_KEY_PLACE}>
        Add it in Account
      </Link>
      .
    </p>
  );
}

/** More company job boards, folded away: the starter list and the boards of the person's own
 * postings, each one click. Nothing is added by itself, and nothing is asked before it opens. */
function BoardIdeas({
  boards,
  disabled,
  onAdd,
}: {
  boards: readonly CompanyBoard[] | null;
  disabled: boolean;
  onAdd: (boards: CompanyBoard[]) => void;
}) {
  return (
    <AdvancedSection title="More company job boards">
      <BoardIdeasList boards={boards} disabled={disabled} onAdd={onAdd} />
    </AdvancedSection>
  );
}

function BoardIdeasList({
  boards,
  disabled,
  onAdd,
}: {
  boards: readonly CompanyBoard[] | null;
  disabled: boolean;
  onAdd: (boards: CompanyBoard[]) => void;
}) {
  const starter = useStarterBoards();
  const suggestions = useQuery({
    queryKey: suggestionsKey,
    queryFn: ({ signal }) => getBoardSuggestions(signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const inList = new Set((boards ?? []).map(boardKey));
  const fromPostings = (suggestions.data?.boards ?? [])
    .map(savedBoard)
    .filter((board) => !inList.has(boardKey(board)));
  const popular = (starter.data?.boards ?? [])
    .map(savedBoard)
    .filter(
      (board) =>
        !inList.has(boardKey(board)) &&
        !fromPostings.some((item) => boardKey(item) === boardKey(board)),
    );
  if (starter.isPending || suggestions.isPending)
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading ideas…
      </p>
    );
  if (!fromPostings.length && !popular.length)
    return (
      <p className="text-sm text-muted-foreground">
        No more ideas right now. Paste the address of any company job board
        above.
      </p>
    );
  return (
    <>
      {fromPostings.length ? (
        <IdeaChips
          title="From your postings"
          boards={fromPostings}
          disabled={disabled}
          onAdd={onAdd}
        />
      ) : null}
      {popular.length ? (
        <IdeaChips
          title="Popular boards"
          boards={popular}
          disabled={disabled}
          onAdd={onAdd}
          addAll
        />
      ) : null}
    </>
  );
}

function IdeaChips({
  title,
  boards,
  disabled,
  onAdd,
  addAll = false,
}: {
  title: string;
  boards: CompanyBoard[];
  disabled: boolean;
  onAdd: (boards: CompanyBoard[]) => void;
  addAll?: boolean;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{title}</p>
        {addAll ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => {
              onAdd(boards);
            }}
          >
            Add all {boards.length}
          </Button>
        ) : null}
      </div>
      <ul aria-label={title} className="flex flex-wrap gap-2">
        {boards.map((board) => (
          <li key={boardKey(board)}>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="rounded-full"
              disabled={disabled}
              aria-label={`Add ${board.company} (${ATS_NAMES[board.ats]})`}
              onClick={() => {
                onAdd([board]);
              }}
            >
              <Plus aria-hidden="true" />
              {board.company}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
