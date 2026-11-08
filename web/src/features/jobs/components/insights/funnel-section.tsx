import { Bar, BarChart, LabelList, XAxis, YAxis } from "recharts";

import { Fact, Facts } from "@/components/blocks/facts";
import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";
import { ChartContainer, type ChartConfig } from "@/components/ui/chart";
import { formatCount } from "@/lib/format";

import type { Insights } from "../../api/insights-api";
import { BENCHMARKS, fewApplications, rateText } from "../../helpers/insights";

const CONFIG = {
  count: { label: "Postings", color: "var(--brand)" },
} satisfies ChartConfig;

/**
 * Found, Liked, Applied, Answered and Interview as bars of a shadcn Chart, the rates under them
 * from 20 applications only, then the dated benchmarks beside those rates, and the counts that
 * leave the funnel (rejected, passed, waiting).
 */
export function FunnelSection({ funnel }: { funnel: Insights["funnel"] }) {
  const stages = [
    { stage: "Found", count: funnel.found },
    { stage: "Liked", count: funnel.liked },
    { stage: "Applied", count: funnel.applied },
    { stage: "Answered", count: funnel.answered },
    { stage: "Interview", count: funnel.interview },
  ];
  const rated = funnel.answer_rate !== null;
  return (
    <Section
      title="From found to interview"
      description="Each step counts the postings that reached it at least once. Answered means an interview or a rejection came back."
    >
      <Card>
        <CardContent className="flex flex-col gap-6">
          <ChartContainer
            config={CONFIG}
            role="img"
            aria-label={stages
              .map((one) => `${one.stage}: ${formatCount(one.count)}`)
              .join(", ")}
            className="aspect-auto h-48 w-full"
          >
            <BarChart
              data={stages}
              layout="vertical"
              accessibilityLayer={false}
              margin={{ top: 0, right: 48, bottom: 0, left: 0 }}
            >
              <XAxis type="number" hide />
              <YAxis
                type="category"
                dataKey="stage"
                tickLine={false}
                axisLine={false}
                width={80}
              />
              <Bar
                dataKey="count"
                fill="var(--color-count)"
                radius={4}
                isAnimationActive={false}
                minPointSize={(value) => (Number(value) > 0 ? 3 : 0)}
              >
                <LabelList
                  dataKey="count"
                  position="right"
                  formatter={(value) => formatCount(Number(value))}
                  className="fill-foreground tabular-nums"
                  fontSize={12}
                />
              </Bar>
            </BarChart>
          </ChartContainer>
          <Facts className="sm:grid-cols-4">
            <Fact label="Answer rate">{rateText(funnel.answer_rate)}</Fact>
            <Fact label="Interview rate">
              {rateText(funnel.interview_rate)}
            </Fact>
            <Fact label="Rejected">{formatCount(funnel.rejected)}</Fact>
            <Fact label="Waiting for an answer">
              {formatCount(funnel.waiting)}
            </Fact>
            <Fact label="Passed on Discover">{formatCount(funnel.passed)}</Fact>
          </Facts>
          {rated ? (
            <p className="max-w-prose text-sm text-muted-foreground">
              For context only, directional figures from mostly US mass
              application platforms:{" "}
              {BENCHMARKS.map(
                (one) =>
                  `${one.typical} of applications ${one.what} (${one.range}, ${one.source}, ${String(one.year)})`,
              ).join(", and ")}
              .
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              {fewApplications(funnel.applied)}
            </p>
          )}
        </CardContent>
      </Card>
    </Section>
  );
}
