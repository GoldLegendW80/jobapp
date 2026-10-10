/**
 * /runs: the user's agent runs, newest first, with status, agent, job, tokens, cost and
 * duration; each opens /runs/:id, the live RunPanel. Outside production the server also offers
 * test agents (GET /api/agents, `test_only`): then a "Test run" card starts one, which is how the
 * run panel is exercised before a real agent exists (A3 adds the first).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Briefcase,
  FlaskConical,
  LoaderCircle,
  Play,
} from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { formatDateTime } from "@/features/jobs/format";
import { describeError, errorDetail } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { elapsed, formatCost, formatDuration, formatTokens } from "./format";
import { RunPanel, StatusBadge } from "./run-panel";
import { useNow } from "./use-now";
import {
  agentsKey,
  getRun,
  listAgents,
  listRuns,
  runKey,
  runsKey,
  startRun,
  type Agent,
  type Run,
} from "./runs-api";

/** Recorded streams the fake backend can replay (api/app/agents/fake_streams/). */
const TEST_SCRIPTS = [
  ["echo", "Short answer"],
  ["tool", "Reads a job with get_job"],
  ["slow", "Thirty steps, one a second"],
  ["forbidden", "Tries forbidden tools"],
  ["loop", "Hits the turn limit"],
  ["crash", "The process dies"],
] as const;

function TestRunCard({ agents }: { agents: Agent[] }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const ids = { agent: useId(), message: useId(), script: useId() };
  const [agent, setAgent] = useState(agents[0]?.name ?? "");
  const [message, setMessage] = useState("Hello from the run panel");
  const [script, setScript] = useState("slow");
  const start = useMutation({
    mutationFn: () =>
      startRun({ agent, input: { message: message.trim(), script } }),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
      void navigate(`/runs/${run.id}`);
    },
    onError: (error) => {
      toast.error(
        `The run did not start. ${errorDetail(error) ?? describeError(error)}`,
      );
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (message.trim()) start.mutate();
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FlaskConical aria-hidden="true" className="size-4" />
          Test run
        </CardTitle>
        <CardDescription>
          Test agents exist only outside production. With the fake backend the
          run replays the recorded stream you pick; with the real one, it calls
          the model with your own credential.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor={ids.agent}>Agent</Label>
            <NativeSelect
              id={ids.agent}
              value={agent}
              onChange={(e) => {
                setAgent(e.target.value);
              }}
            >
              {agents.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={ids.script}>Recorded stream</Label>
            <NativeSelect
              id={ids.script}
              value={script}
              onChange={(e) => {
                setScript(e.target.value);
              }}
            >
              {TEST_SCRIPTS.map(([name, label]) => (
                <option key={name} value={name}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-2 sm:col-span-2">
            <Label htmlFor={ids.message}>Message</Label>
            <Input
              id={ids.message}
              value={message}
              maxLength={2000}
              onChange={(e) => {
                setMessage(e.target.value);
              }}
            />
          </div>
          <div className="sm:col-span-2">
            <Button
              type="submit"
              disabled={start.isPending || !message.trim() || !agent}
              aria-busy={start.isPending}
            >
              {start.isPending ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : (
                <Play aria-hidden="true" />
              )}
              Start test run
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

/** The job a run worked on, as a link to it (A3); a deleted job is named as such. */
export function RunJobLink({ run }: { run: Run }) {
  if (run.job) {
    return (
      <Link
        to={`/jobs/${run.job.id}`}
        className="inline-flex w-fit min-w-0 items-center gap-1 text-sm underline underline-offset-4"
      >
        <Briefcase aria-hidden="true" className="size-3.5 shrink-0" />
        <span className="truncate">
          {run.job.title} at {run.job.company}
        </span>
      </Link>
    );
  }
  if (run.job_id) {
    return (
      <span className="text-sm text-muted-foreground">
        On a job that no longer exists
      </span>
    );
  }
  return null;
}

function RunRow({ run, now }: { run: Run; now: number }) {
  // The job link sits under the run's link, never inside it.
  return (
    <li className="flex flex-col rounded-lg border">
      <Link
        to={`/runs/${run.id}`}
        className="flex flex-col gap-2 rounded-lg p-4 transition-colors hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none sm:flex-row sm:items-center sm:gap-4"
      >
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{run.agent}</span>
            <StatusBadge status={run.status} />
          </span>
          <span className="truncate text-sm text-muted-foreground">
            {formatDateTime(run.created_at)}
            {run.error ? `: ${run.error}` : ""}
          </span>
        </span>
        <span className="grid grid-cols-3 gap-3 text-sm tabular-nums sm:w-96">
          <span>{formatTokens(run.input_tokens, run.output_tokens)}</span>
          <span>{formatCost(run.cost_usd)}</span>
          <span>{formatDuration(elapsed(run, now))}</span>
        </span>
      </Link>
      {run.job_id ? (
        <span className="flex min-w-0 px-4 pb-3">
          <RunJobLink run={run} />
        </span>
      ) : null}
    </li>
  );
}

export function RunsPage() {
  useTitle("Agent runs");
  const runs = useQuery({
    queryKey: runsKey,
    queryFn: ({ signal }) => listRuns(signal),
    refetchInterval: (query) =>
      query.state.data?.some(
        (r) => r.status === "queued" || r.status === "running",
      )
        ? 3000
        : false,
  });
  const agents = useQuery({
    queryKey: agentsKey,
    queryFn: ({ signal }) => listAgents(signal),
  });
  const testAgents = (agents.data ?? []).filter((a) => a.test_only);
  const now = useNow(runs.data?.some((r) => r.status === "running") ?? false);
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Agent runs</h1>
        <p className="text-muted-foreground">
          Every run of your agents, with what it cost and how long it took.
        </p>
      </header>
      {testAgents.length ? <TestRunCard agents={testAgents} /> : null}
      <section aria-labelledby="runs-heading" className="flex flex-col gap-3">
        <h2 id="runs-heading" className="text-lg font-semibold">
          Runs
        </h2>
        {runs.isPending ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Loading runs
          </p>
        ) : runs.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              The runs could not be loaded. {describeError(runs.error)}
            </AlertDescription>
          </Alert>
        ) : runs.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No run yet. Agents you start on a job appear here.
          </p>
        ) : (
          <ul aria-label="Runs" className="flex flex-col gap-2">
            {runs.data.map((run) => (
              <RunRow key={run.id} run={run} now={now} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** /runs/:id: one run, live. */
export function RunPage() {
  useTitle("Agent run");
  const { id = "" } = useParams();
  // The same query as the panel's (shared cache): only read here for the job link.
  const detail = useQuery({
    queryKey: runKey(id),
    queryFn: ({ signal }) => getRun(id, signal),
  });
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Link
          to="/runs"
          className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft aria-hidden="true" className="size-4" />
          All runs
        </Link>
        {detail.data ? <RunJobLink run={detail.data} /> : null}
      </div>
      <RunPanel key={id} runId={id} />
    </div>
  );
}
