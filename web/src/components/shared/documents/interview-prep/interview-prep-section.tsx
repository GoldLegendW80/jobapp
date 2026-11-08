import { Link } from "react-router";

import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { JobDetail } from "@/features/jobs/api/jobs-api";

import { useInterviewPrep } from "@/features/documents/api/use-interview-prep";
import { PrepareButton } from "./prepare-button";

/**
 * "Interview" on the posting page: Prepare for the interview, offered first once the posting's
 * status is interview and on demand before; once prepared, its state and the link to the page.
 * Nothing is written without the click (D29).
 */
export function InterviewPrepSection({ job }: { job: JobDetail }) {
  const prep = useInterviewPrep(job.id);
  const invited = job.status === "interview";
  const page = `/jobs/${job.id}/interview`;
  return (
    <Section title="Interview">
      {prep.isPending ? (
        <LoadingCard>Loading the interview preparation</LoadingCard>
      ) : prep.isError ? (
        <LoadError query={prep}>
          The interview preparation could not be loaded.
        </LoadError>
      ) : (
        <Card>
          <CardContent className="flex flex-col gap-3 text-sm">
            {prep.data.document === null ? (
              <>
                <p className="text-muted-foreground">
                  {invited
                    ? "You are invited to an interview. Get the questions to expect, your gaps, your stories and questions to ask."
                    : "When you are invited, prepare the questions to expect, your gaps, your stories and questions to ask."}
                </p>
                <PrepareButton
                  jobId={job.id}
                  label="Prepare for the interview"
                  variant={invited ? "default" : "outline"}
                />
              </>
            ) : prep.data.writing?.status === "running" ? (
              <p className="flex items-center gap-2" role="status">
                <Spinner aria-hidden="true" role={undefined} />
                Preparing your interview
              </p>
            ) : (
              <>
                <p className="text-muted-foreground">
                  {prep.data.writing?.status === "failed"
                    ? "The last preparation failed. Open it to try again."
                    : `Ready: ${String(prep.data.document.draft.expected.length)} questions, ${String(prep.data.document.draft.stories.length)} stories.`}
                </p>
                <Button asChild size="sm" className="self-start">
                  <Link to={page}>Open the preparation</Link>
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      )}
    </Section>
  );
}
