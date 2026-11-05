import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronRight,
  CircleAlert,
  Minus,
  Plus,
  RotateCw,
  ShieldCheck,
} from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { diffLines } from "@/features/profile/diff";
import { isApiError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";
import { cn } from "@/lib/utils";

import { AdminTabs } from "./admin-tabs";
import {
  agentKey,
  agentsKey,
  changesOf,
  duration,
  problemsOf,
  readAgent,
  REVERT_NOTE,
  saveVersion,
  type AgentPage,
  type Editable,
  type EditableField,
  type NewVersion,
} from "./agents-api";
import { message } from "./errors";
import { Moment } from "./moment";
import { exactTime } from "./relative-time";

const KIND_LABEL: Record<string, string> = {
  anthropic_api_key: "With an API key",
  claude_subscription: "With a Claude subscription",
};

/** How "The code's <field> changed" names each editable field. */
const FIELD_LABEL: Record<EditableField, string> = {
  description: "description",
  system_prompt: "prompt",
  default_message: "default message",
  model: "model",
  model_by_credential: "model per credential kind",
  effort: "effort",
  max_turns: "max turns",
  wall_time_s: "wall time",
  tools: "tools",
};

/** When a run started, or that it has not yet (a queued run). */
const started = (at: string | null) =>
  at === null ? "Not started" : exactTime(at);

/** A code's value in words, for the notice of a field the code changed. */
function shown(value: Editable[EditableField]): string {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  if (value === null) return "Claude Code's default";
  if (typeof value === "object") {
    const kinds = Object.entries(value).map(
      ([kind, model]) => `${KIND_LABEL[kind] ?? kind}, ${model}`,
    );
    return kinds.length ? kinds.join("; ") : "the default model";
  }
  return value === "" ? "none" : String(value);
}

/** Admin, Agents, one agent: everything that makes it, and its editable parts. */
export function AdminAgentPage() {
  const { name = "" } = useParams();
  useTitle(`Agent ${name}`);
  const agent = useQuery({
    queryKey: agentKey(name),
    queryFn: ({ signal }) => readAgent(name, signal),
  });
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <Link
        to="/admin/agents"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        All agents
      </Link>
      <AdminTabs />
      {agent.isPending ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-lg border px-4 py-8 text-center text-sm text-muted-foreground"
        >
          <span className="delayed-appear inline-flex items-center gap-2">
            <Spinner aria-hidden="true" role={undefined} />
            Loading the agent
          </span>
        </p>
      ) : agent.data ? (
        // Keyed by agent: the draft is set from the page once, so another agent's page
        // starts from its own values, never from the last one's draft.
        <AgentView key={agent.data.name} page={agent.data} />
      ) : (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col gap-3">
            <p>
              {isApiError(agent.error) && agent.error.status === 404
                ? "No agent has this name."
                : `The agent could not be loaded. ${message(agent.error)}`}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void agent.refetch()}
              disabled={agent.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}

function AgentView({ page }: { page: AgentPage }) {
  const queryClient = useQueryClient();
  // The draft starts from what runs use now, and stays as typed across refetches; only a
  // save or revert made here, or "Start again", replaces it.
  const [draft, setDraft] = useState<Editable>(page.effective);
  const [base, setBase] = useState<number | null>(page.version);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string>();
  const [reverting, setReverting] = useState(false);
  const problems = problemsOf(draft, page.choices);
  const changes = changesOf(page.code, draft);
  const unsaved =
    JSON.stringify(changes) !==
    JSON.stringify(changesOf(page.code, page.effective));

  const mutation = useMutation({
    mutationFn: (body: NewVersion) => saveVersion(page.name, body),
    onSuccess: (next) => {
      queryClient.setQueryData(agentKey(page.name), next);
      void queryClient.invalidateQueries({ queryKey: agentsKey, exact: true });
      setDraft(next.effective);
      setBase(next.version);
      setNote("");
      setError(undefined);
      setReverting(false);
      toast.success(
        `Version ${String(next.version)} saved. The next run of ${page.name} uses it.`,
      );
    },
    onError: (failure) => {
      setError(message(failure));
    },
  });
  const set = (patch: Partial<Editable>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };
  const newer = page.version !== base;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">
          {page.name}
        </h1>
        <p className="text-muted-foreground">{page.effective.description}</p>
        <div className="flex flex-wrap gap-1">
          {page.test_only ? <Badge variant="outline">Test only</Badge> : null}
          {page.internal ? <Badge variant="outline">Internal</Badge> : null}
          {page.private_io ? <Badge variant="outline">Private</Badge> : null}
          <Badge variant="secondary">
            {Object.keys(page.saved).length && page.version !== null
              ? `Runs use version ${String(page.version)}`
              : "Runs use the code's definition"}
          </Badge>
        </div>
      </header>

      {page.code_changed.length ? (
        <CodeChanged
          page={page}
          onRevert={() => {
            setError(undefined);
            setReverting(true);
          }}
          busy={mutation.isPending}
        />
      ) : null}

      <Alert>
        <ShieldCheck aria-hidden="true" />
        <AlertDescription>{page.guards}</AlertDescription>
      </Alert>

      <Section id="identity" title="Identity">
        <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-muted-foreground">Module</dt>
          <dd>
            <code className="text-xs [overflow-wrap:anywhere]">
              {page.module}
            </code>
          </dd>
          <dt className="text-muted-foreground">Started by</dt>
          <dd>{page.starter}</dd>
        </dl>
        <ul className="list-disc pl-5 text-sm">
          {page.flags.map((flag) => (
            <li key={flag}>{flag}</li>
          ))}
        </ul>
        <Field
          label="Description"
          error={problems.description}
          code={page.code.description}
          changed={draft.description !== page.code.description}
        >
          {(id, described) => (
            <Input
              id={id}
              aria-describedby={described}
              value={draft.description}
              maxLength={page.choices.description_max}
              onChange={(event) => {
                set({ description: event.target.value });
              }}
            />
          )}
        </Field>
      </Section>

      <Section id="input" title="Parameters it takes">
        {page.input_fields.length ? (
          <Table aria-label="Input fields">
            <TableHeader>
              <TableRow>
                {[
                  "Field",
                  "Type",
                  "Required",
                  "Default",
                  "Limits",
                  "Description",
                ].map((head) => (
                  <TableHead key={head}>{head}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.input_fields.map((field) => (
                <TableRow key={field.name} className="align-top">
                  <TableHead>
                    <code>{field.name}</code>
                  </TableHead>
                  <TableCell label="Type">{field.type}</TableCell>
                  <TableCell label="Required">
                    {field.required ? "Yes" : "No"}
                  </TableCell>
                  <TableCell label="Default">
                    {field.default === null || field.default === undefined
                      ? "None"
                      : JSON.stringify(field.default)}
                  </TableCell>
                  <TableCell label="Limits">
                    <span className="[overflow-wrap:anywhere]">
                      {field.limits || "None"}
                    </span>
                  </TableCell>
                  <TableCell label="Description">
                    {field.description || "None"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">
            It takes no parameter.
          </p>
        )}
        <Json label="Its JSON schema" value={page.input_schema} />
      </Section>

      <Section id="prompt" title="System prompt">
        <Field
          label="System prompt"
          error={problems.system_prompt}
          hint={`${draft.system_prompt.length.toLocaleString("en")} of ${page.choices.prompt_max.toLocaleString("en")} characters. Only its size is checked, never what it says.`}
          changed={draft.system_prompt !== page.code.system_prompt}
        >
          {(id, described) => (
            <Textarea
              id={id}
              aria-describedby={described}
              rows={16}
              className="font-mono text-xs"
              value={draft.system_prompt}
              onChange={(event) => {
                set({ system_prompt: event.target.value });
              }}
            />
          )}
        </Field>
        <PromptDiff from={page.code.system_prompt} to={draft.system_prompt} />
        <Json label="The code's prompt" value={page.code.system_prompt} text />
      </Section>

      <Section id="first-message" title="First message">
        <p className="text-sm">{page.first_message}</p>
        {page.uses_default_message ? (
          <Field
            label="Default message"
            error={problems.default_message}
            hint="Used when a run's input carries no message of its own."
            code={page.code.default_message || "None"}
            changed={draft.default_message !== page.code.default_message}
          >
            {(id, described) => (
              <Textarea
                id={id}
                aria-describedby={described}
                rows={3}
                value={draft.default_message}
                onChange={(event) => {
                  set({ default_message: event.target.value });
                }}
              />
            )}
          </Field>
        ) : null}
      </Section>

      <Section id="tools" title="Tools">
        <p className="text-sm text-muted-foreground">
          The tools the code gives this agent, as the model sees them. Switch
          one off and the next runs neither offer nor serve it; no tool can be
          added here.
        </p>
        <ul
          aria-label="Tools of this agent"
          className="divide-y rounded-md border"
        >
          {page.tools.map((tool) => (
            <li key={tool.name} className="min-w-0 space-y-1 p-3 text-sm">
              <Label className="flex min-w-0 items-center gap-2">
                <Checkbox
                  checked={draft.tools.includes(tool.name)}
                  onCheckedChange={(checked) => {
                    const on = checked === true;
                    set({
                      tools: page.code.tools.filter((name) =>
                        name === tool.name ? on : draft.tools.includes(name),
                      ),
                    });
                  }}
                />
                <code className="[overflow-wrap:anywhere]">{tool.name}</code>
                {tool.on !== draft.tools.includes(tool.name) ? (
                  <Badge variant="outline">Not saved</Badge>
                ) : null}
              </Label>
              <p className="text-muted-foreground [overflow-wrap:anywhere]">
                {tool.description ??
                  "Not described yet: the worker describes the tools it has loaded, a minute after it starts at most."}
              </p>
              <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
                <code>{tool.full_name}</code>
                {tool.module ? ` in ${tool.module}` : null}
              </p>
              {tool.input_schema ? (
                <Json label="Its input schema" value={tool.input_schema} />
              ) : null}
            </li>
          ))}
        </ul>
      </Section>

      <Section id="model" title="Model and limits">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Model"
            code={page.code.model}
            changed={draft.model !== page.code.model}
          >
            {(id, described) => (
              <NativeSelect
                id={id}
                aria-describedby={described}
                value={draft.model}
                onChange={(event) => {
                  set({ model: event.target.value });
                }}
              >
                {page.choices.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </NativeSelect>
            )}
          </Field>
          {page.choices.credential_kinds.map((kind) => (
            <Field
              key={kind}
              label={KIND_LABEL[kind] ?? kind}
              code={page.code.model_by_credential[kind] ?? "The default model"}
              changed={
                draft.model_by_credential[kind] !==
                page.code.model_by_credential[kind]
              }
            >
              {(id, described) => (
                <NativeSelect
                  id={id}
                  aria-describedby={described}
                  value={draft.model_by_credential[kind] ?? ""}
                  onChange={(event) => {
                    const next = Object.fromEntries(
                      Object.entries(draft.model_by_credential).filter(
                        ([key]) => key !== kind,
                      ),
                    );
                    if (event.target.value) next[kind] = event.target.value;
                    set({ model_by_credential: next });
                  }}
                >
                  <option value="">The default model</option>
                  {page.choices.models.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
          ))}
          <Field
            label="Effort"
            code={page.code.effort ?? "Claude Code's default"}
            changed={draft.effort !== page.code.effort}
          >
            {(id, described) => (
              <NativeSelect
                id={id}
                aria-describedby={described}
                value={draft.effort ?? ""}
                onChange={(event) => {
                  set({ effort: event.target.value || null });
                }}
              >
                {page.code.effort === null ? (
                  <option value="">Claude Code&apos;s default</option>
                ) : null}
                {page.choices.efforts.map((effort) => (
                  <option key={effort} value={effort}>
                    {effort}
                  </option>
                ))}
              </NativeSelect>
            )}
          </Field>
          <Field
            label="Max turns"
            error={problems.max_turns}
            hint={
              page.turn_budgeted
                ? "A run's input may lower it with its own turn budget."
                : undefined
            }
            code={String(page.code.max_turns)}
            changed={draft.max_turns !== page.code.max_turns}
          >
            {(id, described) => (
              <Input
                id={id}
                aria-describedby={described}
                type="number"
                inputMode="numeric"
                min={1}
                max={page.choices.turns_max}
                value={Number.isNaN(draft.max_turns) ? "" : draft.max_turns}
                onChange={(event) => {
                  set({
                    max_turns:
                      event.target.value === ""
                        ? Number.NaN
                        : Number(event.target.value),
                  });
                }}
              />
            )}
          </Field>
          <Field
            label="Wall time, in seconds"
            error={problems.wall_time_s}
            hint={`At most ${page.choices.wall_time_max_s.toLocaleString("en")} seconds (${duration(page.choices.wall_time_max_s)}) for this agent. ${page.choices.wall_time_where} Lower or raise it within that.`}
            code={String(page.code.wall_time_s)}
            changed={draft.wall_time_s !== page.code.wall_time_s}
          >
            {(id, described) => (
              <Input
                id={id}
                aria-describedby={described}
                type="number"
                inputMode="decimal"
                min={page.choices.wall_time_min_s}
                max={page.choices.wall_time_max_s}
                value={Number.isNaN(draft.wall_time_s) ? "" : draft.wall_time_s}
                onChange={(event) => {
                  set({
                    wall_time_s:
                      event.target.value === ""
                        ? Number.NaN
                        : Number(event.target.value),
                  });
                }}
              />
            )}
          </Field>
        </div>
        <h3 className="text-sm font-medium">What ends a run</h3>
        <ul className="list-disc pl-5 text-sm">
          {page.ends.map((end) => (
            <li key={end}>{end}</li>
          ))}
        </ul>
      </Section>

      <Section id="save" title="Save">
        {newer ? (
          <Alert>
            <CircleAlert aria-hidden="true" />
            <AlertDescription className="flex flex-col gap-2">
              <p>
                Version {String(page.version)} was saved since you opened this
                page. Start again from it before you save.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="self-start"
                onClick={() => {
                  setDraft(page.effective);
                  setBase(page.version);
                  setError(undefined);
                }}
              >
                Start again from version {String(page.version)}
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-col gap-2">
          <Label htmlFor="agent-note">Note, optional</Label>
          <Input
            id="agent-note"
            value={note}
            maxLength={page.choices.note_max}
            onChange={(event) => {
              setNote(event.target.value);
            }}
          />
        </div>
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={
              !unsaved || Object.keys(problems).length > 0 || mutation.isPending
            }
            aria-busy={mutation.isPending && !reverting}
            onClick={() => {
              setError(undefined);
              mutation.mutate({
                changes,
                note: note.trim() || null,
                base_version: base,
              });
            }}
          >
            {mutation.isPending && !reverting ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : null}
            Save as a new version
          </Button>
          <Button
            variant="outline"
            disabled={!Object.keys(page.saved).length || mutation.isPending}
            onClick={() => {
              setError(undefined);
              setReverting(true);
            }}
          >
            Revert to the code&apos;s version
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          {unsaved
            ? Object.keys(problems).length
              ? "Fix the fields marked above to save."
              : "Unsaved changes. Saving makes a new version, used from the next run on."
            : "Nothing to save: the fields above are what runs use now."}
        </p>
      </Section>

      <Section id="claude-code" title="How Claude Code is run for it">
        <p className="text-sm text-muted-foreground">
          The options the runner hands the Claude Agent SDK for each run, as
          they are. None of them is editable, except through the fields above.
        </p>
        <pre
          aria-label="Claude Agent SDK options"
          className="max-h-96 overflow-auto rounded-md border bg-muted p-3 text-xs"
        >
          {JSON.stringify(page.claude_code, null, 2)}
        </pre>
        <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-muted-foreground">Environment</dt>
          <dd>{page.environment}</dd>
          <dt className="text-muted-foreground">Tool gate</dt>
          <dd>{page.gate}</dd>
          <dt className="text-muted-foreground">Skills</dt>
          <dd>{page.skills}</dd>
        </dl>
      </Section>

      <Section id="runs" title="Recent runs">
        {page.runs.length ? (
          <Table aria-label="Recent runs, newest first">
            <TableHeader>
              <TableRow>
                {[
                  "Started",
                  "Person",
                  "Status",
                  "Model",
                  "Version",
                  "Turns",
                  "Tokens",
                ].map((head) => (
                  <TableHead key={head}>{head}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.runs.map((run) => (
                <TableRow key={run.id}>
                  <TableHead className="font-normal">
                    {run.mine ? (
                      <Link
                        to={`/runs/${run.id}`}
                        className="text-primary underline-offset-4 hover:underline"
                      >
                        {started(run.started_at)}
                      </Link>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span>{started(run.started_at)}</span>
                        </TooltipTrigger>
                        <TooltipContent>
                          Another person's run: its page is theirs
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </TableHead>
                  <TableCell label="Person">
                    <span className="flex min-w-0 flex-col">
                      <span className="break-words">
                        {run.user_name || run.user_email}
                      </span>
                      <span className="text-xs break-all text-muted-foreground">
                        {run.user_email}
                      </span>
                    </span>
                  </TableCell>
                  <TableCell label="Status">{run.status}</TableCell>
                  <TableCell label="Model">
                    {run.model ?? "Not known"}
                  </TableCell>
                  <TableCell label="Version">
                    {run.version === null
                      ? "The code's"
                      : `Version ${String(run.version)}`}
                  </TableCell>
                  <TableCell label="Turns">
                    {run.turns ?? "Not known"}
                  </TableCell>
                  <TableCell label="Tokens">
                    {run.input_tokens === null && run.output_tokens === null
                      ? "Not known"
                      : `${String(run.input_tokens ?? 0)} in, ${String(run.output_tokens ?? 0)} out`}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">No run yet.</p>
        )}
        {page.private_io ? (
          <p className="text-sm text-muted-foreground">
            A private agent&apos;s runs keep their numbers only, never what they
            read or wrote.
          </p>
        ) : null}
      </Section>

      <Section id="history" title="History of versions">
        {page.versions.length ? (
          <ol
            aria-label="Versions, newest first"
            className="divide-y rounded-md border"
          >
            {page.versions.map((version) => (
              <li
                key={version.version}
                className="flex flex-col gap-1 p-3 text-sm"
              >
                <p className="font-medium">
                  Version {String(version.version)}
                  {version.version === page.version ? " (runs use it)" : ""}
                </p>
                <p className="text-muted-foreground">
                  {version.fields.length
                    ? `Changes ${version.fields.join(", ")}.`
                    : "No change: the code's definition."}{" "}
                  Saved by {version.saved_by ?? "an account that is gone"}{" "}
                  <Moment at={version.saved_at} />.
                </p>
                {version.note ? (
                  <p className="[overflow-wrap:anywhere]">{version.note}</p>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted-foreground">
            No version saved yet: runs use the code&apos;s definition.
          </p>
        )}
      </Section>

      <AlertDialog
        open={reverting}
        onOpenChange={(open) => {
          if (!open && !mutation.isPending) setReverting(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Revert {page.name} to the code&apos;s version?
            </AlertDialogTitle>
            <AlertDialogDescription>
              A new version with no change is saved, so the history stays whole.
              The next run uses the code&apos;s definition, and unsaved changes
              on this page are dropped.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutation.isPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={mutation.isPending}
              aria-busy={mutation.isPending}
              onClick={() => {
                mutation.mutate({
                  changes: {},
                  note: REVERT_NOTE,
                  base_version: base,
                });
              }}
            >
              {mutation.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Revert
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2 id={`${id}-title`} className="text-lg font-semibold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Field({
  label,
  error,
  hint,
  code,
  changed,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  code?: string;
  changed: boolean;
  children: (id: string, described: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children(id, `${id}-help`)}
      <div id={`${id}-help`} className="flex flex-col gap-1 text-sm">
        {error ? <p className="text-destructive">{error}</p> : null}
        {hint ? <p className="text-muted-foreground">{hint}</p> : null}
        {changed && code !== undefined ? (
          <p className="text-muted-foreground [overflow-wrap:anywhere]">
            The code&apos;s: {code}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Json({
  label,
  value,
  text = false,
}: {
  label: string;
  value: unknown;
  text?: boolean;
}) {
  return (
    <Collapsible className="rounded-md border text-sm">
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          className="group/fold h-auto w-full justify-start gap-1.5 rounded-md px-3 py-2 text-left font-normal whitespace-normal text-muted-foreground has-[>svg]:px-3"
        >
          <ChevronRight
            aria-hidden="true"
            className="transition-transform group-data-[state=open]/fold:rotate-90 motion-reduce:transition-none"
          />
          {label}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="max-h-96 overflow-auto border-t bg-muted p-3 text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">
          {text ? String(value) : JSON.stringify(value, null, 2)}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** The notice of the fields the code changed since the version that runs was saved. */
function CodeChanged({
  page,
  onRevert,
  busy,
}: {
  page: AgentPage;
  onRevert: () => void;
  busy: boolean;
}) {
  const fields = page.code_changed.filter(
    (field): field is EditableField => field in FIELD_LABEL,
  );
  return (
    <Alert>
      <CircleAlert aria-hidden="true" />
      <AlertDescription className="flex min-w-0 flex-col gap-3">
        {fields.map((field) =>
          field === "system_prompt" ? (
            <div key={field} className="flex min-w-0 flex-col gap-2">
              <p>
                The code&apos;s prompt changed since you saved this version.
              </p>
              {page.code_prompt_then !== null ? (
                <PromptDiff
                  from={page.code_prompt_then}
                  to={page.code.system_prompt}
                  title="What the code changed in its prompt"
                />
              ) : null}
            </div>
          ) : (
            <p key={field} className="[overflow-wrap:anywhere]">
              The code&apos;s {FIELD_LABEL[field]} changed since you saved this
              version. The code&apos;s value now: {shown(page.code[field])}.
            </p>
          ),
        )}
        <p>
          Runs still use version {String(page.version)}. Edit it below, or go
          back to the code&apos;s version.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="self-start"
          disabled={busy}
          onClick={onRevert}
        >
          Revert to the code&apos;s version
        </Button>
      </AlertDescription>
    </Alert>
  );
}

function PromptDiff({
  from,
  to,
  title = "Changes from the code's prompt",
}: {
  from: string;
  to: string;
  title?: string;
}) {
  if (from === to)
    return (
      <p className="text-sm text-muted-foreground">
        The same as the code&apos;s prompt.
      </p>
    );
  const ops = diffLines(from, to).filter((op) => op.kind !== "same");
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <h3 className="text-sm font-medium">{title}</h3>
      <ul
        aria-label={title}
        className="max-h-96 overflow-auto rounded-md border font-mono text-xs"
      >
        {ops.map((op, index) => (
          <li
            key={index}
            data-kind={op.kind}
            className={cn(
              "flex gap-2 px-2 whitespace-pre-wrap [overflow-wrap:anywhere]",
              op.kind === "added" &&
                "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
              op.kind === "removed" &&
                "bg-red-50 text-red-900 dark:bg-red-950/50 dark:text-red-200",
            )}
          >
            <span aria-hidden="true" className="w-3 shrink-0 select-none">
              {op.kind === "added" ? (
                <Plus className="mt-1 size-3" />
              ) : (
                <Minus className="mt-1 size-3" />
              )}
            </span>
            <span className="sr-only">
              {op.kind === "added" ? "Added: " : "Removed: "}
            </span>
            {op.text || " "}
          </li>
        ))}
      </ul>
    </div>
  );
}
