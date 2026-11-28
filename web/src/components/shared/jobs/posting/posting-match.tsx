import { Section } from "@/components/blocks/section";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatDate } from "@/lib/format";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { matchBand } from "@/features/jobs/helpers/match-bands";

type Match = NonNullable<JobDetail["match"]>;

/**
 * The assistant's reading of the posting against the career and culture the profile states:
 * two scores with their band, strengths and gaps in the model's words (text only), and the day
 * it was read. Nothing at all for a posting with no reading, so no empty block.
 */
export function PostingMatch({ match }: { match: Match | null | undefined }) {
  if (!match) return null;
  return (
    <Section
      title="Career and culture"
      description="The assistant's reading of this posting against your profile. It is separate from your keyword fit, which counts your profile's words."
    >
      <Card>
        <CardContent className="flex flex-col gap-6">
          <dl className="grid gap-4 sm:grid-cols-2">
            <Score label="Career" score={match.career} />
            <Score label="Culture" score={match.culture} />
          </dl>
          <div className="grid gap-6 sm:grid-cols-2">
            <Points title="Strengths" lines={match.strengths} />
            <Points title="Gaps in the match" lines={match.gaps} />
          </div>
          <p className="max-w-prose text-sm text-muted-foreground">
            Read on {formatDate(match.rated_at)}.
            {match.current
              ? ""
              : " The posting's text changed since, so it will be read again."}
          </p>
        </CardContent>
      </Card>
    </Section>
  );
}

function Score({ label, score }: { label: string; score: number }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-sm font-medium">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2">
        <span className="text-2xl font-semibold tabular-nums">{score}</span>
        <span className="sr-only">out of 100,</span>
        <Badge variant="outline">{matchBand(score)}</Badge>
      </dd>
    </div>
  );
}

function Points({ title, lines }: { title: string; lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <ul
        aria-label={title}
        className="flex list-disc flex-col gap-1.5 pl-5 text-sm"
      >
        {lines.map((line, index) => (
          <li key={index} className="break-words">
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}
