import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { describeError } from "@/lib/api";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { useCheckPosting } from "@/features/jobs/api/use-check-posting";
import { useCloseJob } from "@/features/jobs/api/use-close-job";
import {
  checkWords,
  mayBeClosed,
  NOT_CHECKED,
} from "@/features/jobs/helpers/liveness-words";

/**
 * Is the posting still open, in the posting's side rail: what the last check of its own address
 * found, the page's own sentence, "Mark closed" when the check only proposes it (the person
 * decides), and "Check now". Nothing for a posting without an address.
 */
export function PostingLiveness({ job }: { job: JobDetail }) {
  const check = useCheckPosting(job.id);
  const close = useCloseJob(job);
  if (!job.url) return null;
  const liveness = job.liveness ?? null;
  const proposal = mayBeClosed(job);
  const linkedin = liveness?.reason === "linkedin";
  return (
    <Section title="Still open?">
      <Card>
        <CardContent className="flex flex-col gap-3 text-sm">
          <p data-testid="liveness-words">
            {liveness ? checkWords(liveness, job.status) : NOT_CHECKED}
          </p>
          {liveness?.sentence ? (
            <blockquote className="border-l-2 border-warning pl-3 [overflow-wrap:anywhere]">
              {`“${liveness.sentence}”`}
            </blockquote>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {proposal ? (
              <Button
                disabled={close.isPending}
                onClick={() => {
                  close.mutate();
                }}
              >
                Mark closed
              </Button>
            ) : null}
            {linkedin ? null : (
              <Button
                variant="outline"
                disabled={check.isPending}
                onClick={() => {
                  check.mutate();
                }}
              >
                {check.isPending ? "Checking…" : "Check now"}
              </Button>
            )}
          </div>
          {check.isError ? (
            <p role="alert">{describeError(check.error)}</p>
          ) : null}
          {close.isError ? (
            <p role="alert">{describeError(close.error)}</p>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}
