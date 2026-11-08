import { ListChecks } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Fact, Facts } from "@/components/blocks/facts";
import { LoadError } from "@/components/blocks/load-error";
import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { useModelCredential } from "@/features/agents/api/use-model-credential";
import type { AssessmentView } from "@/features/jobs/api/assessment-api";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import {
  isAssessing,
  useAssessment,
  useStartAssessment,
} from "@/features/jobs/api/use-assessment";
import {
  CONFIDENCE_WORDS,
  gapCount,
} from "@/features/jobs/helpers/assessment-words";
import { formatDate } from "@/lib/format";
import { describeError, errorDetail } from "@/lib/api";

import { FitBadge } from "../badges";

/**
 * Assess this posting, in a posting's side rail beside its documents (SPEC-jobs.md, "Assess this
 * posting"): Assess starts the agent on the person's own credential, a line follows its run, and
 * once it submitted, the verdict, how sure it is, the gaps to prepare and the keyword fit, which
 * the assessment never changes. The whole assessment is folded under the posting
 * (AssessmentDetails).
 */
export function AssessSection({ job }: { job: JobDetail }) {
  const credential = useModelCredential();
  const view = useAssessment(job.id);
  const start = useStartAssessment(job.id, {
    onError: (error) => {
      toast.error(
        `The assessment did not start. ${errorDetail(error) ?? describeError(error)}`,
      );
    },
  });
  const needsId = useId();
  const noCredential = credential.isSuccess && credential.data === null;
  const assessing = isAssessing(view.data);
  const assessment = view.data?.assessment ?? null;

  return (
    <Section
      title="Assess this posting"
      description="An agent weighs what the posting asks against your profile. Your keyword fit stays as it is."
      actions={
        <Button
          variant="outline"
          size="sm"
          disabled={
            credential.isPending ||
            noCredential ||
            view.isPending ||
            assessing ||
            start.isPending
          }
          aria-busy={start.isPending}
          aria-describedby={noCredential ? needsId : undefined}
          onClick={() => {
            start.mutate();
          }}
        >
          {assessing || start.isPending ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <ListChecks aria-hidden="true" />
          )}
          {assessing ? "Assessing" : "Assess"}
        </Button>
      }
    >
      {noCredential ? (
        <p id={needsId} className="text-sm text-muted-foreground">
          Agents run with your own model credential, and none is saved yet.{" "}
          <Link to="/account" className="underline underline-offset-4">
            Add one on the Account page
          </Link>
          .
        </p>
      ) : null}
      {view.isError ? (
        <LoadError query={view}>The assessment could not be loaded.</LoadError>
      ) : null}
      {view.data ? <RunLine view={view.data} /> : null}
      {assessment ? (
        <Card data-testid="assessment-summary">
          <CardContent className="flex flex-col gap-4">
            <p className="text-sm">{assessment.result.verdict}</p>
            <Facts>
              <Fact label="How sure">
                {CONFIDENCE_WORDS[assessment.result.confidence]}
              </Fact>
              <Fact label="Gaps to prepare">{gapCount(assessment.result)}</Fact>
              <Fact label="Keyword fit">
                <FitBadge fit={job.fit} overridden={job.fit_overridden} />
              </Fact>
              <Fact label="Assessed">{formatDate(assessment.created_at)}</Fact>
            </Facts>
            <p className="text-sm text-muted-foreground">
              The whole assessment is under the posting.
            </p>
          </CardContent>
        </Card>
      ) : null}
    </Section>
  );
}

/** What the latest run says when it is not the assessment shown: under way, failed, empty. */
function RunLine({ view }: { view: AssessmentView }) {
  const run = view.run;
  if (run === null || view.assessment?.run_id === run.id) return null;
  if (isAssessing(view))
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Reading the posting and your profile. This takes a minute or two.
      </p>
    );
  if (run.status === "done")
    return (
      <p className="text-sm text-muted-foreground">
        The agent ended without an assessment. Click Assess to try again.
      </p>
    );
  return (
    <Alert variant="destructive">
      <AlertDescription>
        {`The last assessment did not finish. ${run.error ?? "It was stopped."}`}
      </AlertDescription>
    </Alert>
  );
}
