import { FlaskConical, Play } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { describeError, errorDetail } from "@/lib/api";

import type { Agent } from "../api/runs-api";
import { useStartRun } from "../api/use-runs";

/** Recorded streams the fake backend can replay (api/app/agents/fake_streams/). */
const TEST_SCRIPTS = [
  ["echo", "Short answer"],
  ["tool", "Reads a job with get_job"],
  ["slow", "Thirty steps, one a second"],
  ["long_answer", "A long answer, written slowly"],
  ["forbidden", "Tries forbidden tools"],
  ["loop", "Hits the turn limit"],
  ["crash", "The process dies"],
] as const;

/** Starts a run of a test agent, outside production only, and opens it. */
export function TestRunCard({ agents }: { agents: Agent[] }) {
  const navigate = useNavigate();
  const ids = { agent: useId(), message: useId(), script: useId() };
  const [agent, setAgent] = useState(agents[0]?.name ?? "");
  const [message, setMessage] = useState("Hello from the run panel");
  const [script, setScript] = useState("slow");
  const start = useStartRun({
    onSuccess: (run) => {
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
    if (message.trim())
      start.mutate({ agent, input: { message: message.trim(), script } });
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
          <Field className="gap-2">
            <FieldLabel htmlFor={ids.agent}>Agent</FieldLabel>
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
          </Field>
          <Field className="gap-2">
            <FieldLabel htmlFor={ids.script}>Recorded stream</FieldLabel>
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
          </Field>
          <Field className="gap-2 sm:col-span-2">
            <FieldLabel htmlFor={ids.message}>Message</FieldLabel>
            <Input
              id={ids.message}
              value={message}
              maxLength={2000}
              onChange={(e) => {
                setMessage(e.target.value);
              }}
            />
          </Field>
          <div className="sm:col-span-2">
            <Button
              type="submit"
              disabled={start.isPending || !message.trim() || !agent}
              aria-busy={start.isPending}
            >
              {start.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
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
