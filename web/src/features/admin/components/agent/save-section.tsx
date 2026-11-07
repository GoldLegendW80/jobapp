import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";

import type { AgentPage } from "../../api/agents-api";
import type { AgentDraft } from "@/features/admin/api/use-agent-draft";
import { AgentSection } from "./agent-section";

/** The save of the draft as a new version, its note, and the revert to the code's version. */
export function SaveSection({
  page,
  agent,
}: {
  page: AgentPage;
  agent: AgentDraft;
}) {
  const hasProblems = Object.keys(agent.problems).length > 0;
  const savingNew = agent.saving && !agent.reverting;
  return (
    <AgentSection
      title="Save"
      description="Each save is a new version, and the history of versions keeps every one."
    >
      {agent.newer ? (
        <Alert variant="warning">
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
              onClick={agent.startAgain}
            >
              Start again from version {String(page.version)}
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      <Field className="max-w-xl gap-2">
        <FieldLabel htmlFor="agent-note">Note, optional</FieldLabel>
        <Input
          id="agent-note"
          value={agent.note}
          maxLength={page.choices.note_max}
          onChange={(event) => {
            agent.setNote(event.target.value);
          }}
        />
      </Field>
      {agent.error ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{agent.error}</AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={!agent.unsaved || hasProblems || agent.saving}
          aria-busy={savingNew}
          onClick={agent.save}
        >
          {savingNew ? <Spinner aria-hidden="true" role={undefined} /> : null}
          Save as a new version
        </Button>
        <Button
          variant="outline"
          disabled={!Object.keys(page.saved).length || agent.saving}
          onClick={agent.askRevert}
        >
          Revert to the code&apos;s version
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        {agent.unsaved
          ? hasProblems
            ? "Fix the fields marked above to save."
            : "Unsaved changes. Saving makes a new version, used from the next run on."
          : "Nothing to save: the fields above are what runs use now."}
      </p>
    </AgentSection>
  );
}
