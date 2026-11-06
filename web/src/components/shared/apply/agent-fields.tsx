import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { describeError } from "@/lib/api";
import {
  type Application,
  type Chosen,
  type Filled,
  refusalOf,
} from "@/features/apply/api/apply-api";
import { useConfirmFilled } from "@/features/apply/api/use-application-actions";
import { byStep } from "@/features/apply/helpers/agent-fields-state";

const KIND_NOTE: Record<string, string> = {
  file: "File",
  tick: "Tick box",
  choice: "Choice",
};

/**
 * Review first with the apply agent (A20, owner 2026-11-06: "I want to see all the input he
 * filled in but don't matter of the website or whatever, then a button to confirm"): every
 * field the assistant filled, by step, from its own recorded acts, then Confirm and send.
 */
export function AgentFields({
  application,
  filled,
  onChanged,
}: {
  application: Application;
  filled: Filled;
  onChanged: (changed?: Application) => void;
}) {
  const [notice, setNotice] = useState<string | null>(null);
  const waiting = application.state === "ready" && filled.fields_hash !== null;
  const act = useConfirmFilled(application.id, filled.fields_hash, {
    onSuccess: (changed) => {
      setNotice(null);
      onChanged(changed);
    },
    onError: (error) => {
      setNotice(refusalOf(error)?.detail ?? describeError(error));
      onChanged();
    },
  });
  return (
    <section aria-label="What the assistant filled" className="space-y-3">
      <h3 className="text-sm font-medium">
        {waiting
          ? "Check what the assistant filled, then confirm to send it"
          : "What the assistant filled"}
      </h3>
      {byStep(filled.fields).map(([step, fields], stepIndex) => (
        <div key={stepIndex} className="space-y-1">
          <h4 className="text-sm text-muted-foreground">{step}</h4>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Field</TableHead>
                <TableHead scope="col">Value</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {fields.map((field, index) => (
                // Two fields may share a label (a work history's "Company"): keyed by place.
                <TableRow key={index}>
                  <TableCell className="align-top font-medium break-words">
                    {field.label}
                    {KIND_NOTE[field.kind] ? (
                      <span className="block text-xs font-normal text-muted-foreground">
                        {KIND_NOTE[field.kind]}
                      </span>
                    ) : null}
                  </TableCell>
                  <TableCell className="align-top break-words whitespace-pre-wrap">
                    {field.value || (
                      <span className="text-muted-foreground">Empty</span>
                    )}
                    {field.chosen ? (
                      <span className="mt-1 block space-y-1">
                        <Badge variant="secondary">
                          Chosen by the assistant
                        </Badge>
                        <span className="block text-xs text-muted-foreground">
                          {field.chosen}
                        </span>
                      </span>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ))}
      {notice ? (
        <Alert variant="destructive">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}
      {waiting ? (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button
            className="w-full sm:w-fit"
            disabled={act.isPending}
            onClick={() => {
              act.mutate("confirm");
            }}
          >
            {act.isPending && act.variables === "confirm" ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : null}
            Confirm and send
          </Button>
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            disabled={act.isPending}
            onClick={() => {
              act.mutate("cancel");
            }}
          >
            Cancel
          </Button>
        </div>
      ) : null}
      {waiting ? (
        <p className="text-xs text-muted-foreground">
          On Confirm the app clicks &ldquo;{filled.submit}&rdquo; on the form
          the assistant filled, and nothing else.
        </p>
      ) : null}
    </section>
  );
}

/** One click Apply's answers the assistant chose for the person (A20), in the run report. */
export function ChosenAnswers({ chosen }: { chosen: readonly Chosen[] }) {
  if (chosen.length === 0) return null;
  return (
    <section aria-label="Answers the assistant chose" className="space-y-1">
      <h3 className="text-sm font-medium">Answers the assistant chose</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Field</TableHead>
            <TableHead scope="col">Answer</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {chosen.map((row) => (
            <TableRow key={row.label}>
              <TableCell className="align-top font-medium break-words">
                {row.label}
              </TableCell>
              <TableCell className="align-top break-words">
                {row.value}
                <span className="block text-xs text-muted-foreground">
                  {row.why}
                </span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}
