import { Fact, Facts } from "@/components/blocks/facts";
import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";
import { formatCount } from "@/lib/format";

import { passReasonLabel } from "@/features/opportunities/helpers/deck/pass-reasons";
import type { Insights } from "../../api/insights-api";
import { daysText, FLOORS, TOO_FEW } from "../../helpers/insights";

/** From the day an application left to its first interview or rejection, as a median. */
export function FirstAnswerSection({
  first,
}: {
  first: Insights["first_answer"];
}) {
  return (
    <Section
      title="Time to a first answer"
      description={`From the day you applied to the first interview or rejection. The median shows from ${String(FLOORS.MEDIAN_MIN)} dated answers.`}
    >
      <Card>
        <CardContent>
          <Facts>
            <Fact label="Median">
              {first.median_days === null
                ? TOO_FEW
                : daysText(first.median_days)}
            </Fact>
            <Fact label="Dated answers">{formatCount(first.measured)}</Fact>
            <Fact label="Still waiting">{formatCount(first.waiting)}</Fact>
            <Fact label="Answers without a date">
              {formatCount(first.undated)}
            </Fact>
          </Facts>
        </CardContent>
      </Card>
    </Section>
  );
}

/** The reasons tapped after a Pass on Discover, most given first. */
export function PassReasonsSection({
  reasons,
  without,
}: {
  reasons: Insights["pass_reasons"];
  without: number;
}) {
  const passes = without + reasons.reduce((sum, one) => sum + one.count, 0);
  return (
    <Section
      title="Your reasons to pass"
      description="The reasons you tapped after a Pass on Discover. Nothing else reads them."
    >
      <Card>
        <CardContent>
          {passes === 0 ? (
            <p className="text-sm text-muted-foreground">
              No Pass on Discover yet.
            </p>
          ) : (
            <Facts>
              {reasons.map((one) => (
                <Fact key={one.reason} label={passReasonLabel(one.reason)}>
                  {formatCount(one.count)}
                </Fact>
              ))}
              <Fact label="No reason given">{formatCount(without)}</Fact>
            </Facts>
          )}
        </CardContent>
      </Card>
    </Section>
  );
}

/** Jobs J37: the postings the app closed by itself, its check of their page or their date. */
export function ClosedByChecksSection({
  closed,
}: {
  closed: Insights["closed_by_checks"];
}) {
  const total = closed.page + closed.closing_date;
  return (
    <Section
      title="Closed by the app"
      description="Open postings whose page the app found gone or saying they are closed, or whose stated closing date passed. Each one's History says why; a posting you reopen stays open."
    >
      <Card>
        <CardContent>
          {total === 0 ? (
            <p className="text-sm text-muted-foreground">
              None closed by the app yet.
            </p>
          ) : (
            <Facts>
              <Fact label="Page gone or closed">
                {formatCount(closed.page)}
              </Fact>
              <Fact label="Closing date passed">
                {formatCount(closed.closing_date)}
              </Fact>
              <Fact label={`In the last ${String(closed.days)} days`}>
                {formatCount(closed.recent)}
              </Fact>
            </Facts>
          )}
        </CardContent>
      </Card>
    </Section>
  );
}
