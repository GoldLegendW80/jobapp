import type { ReactNode } from "react";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Section } from "@/components/blocks/section";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type {
  AssessedRequirement,
  AssessmentResult,
} from "@/features/jobs/api/assessment-api";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { useAssessment } from "@/features/jobs/api/use-assessment";
import {
  CLARITY_WORDS,
  FINDING_WORDS,
  IMPORTANCE_WORDS,
  MATCH_WORDS,
  SIGNAL_WORDS,
  TIER_WORDS,
} from "@/features/jobs/helpers/assessment-words";
import { formatDate } from "@/lib/format";

/**
 * The posting's last assessment, under the posting and folded (SPEC-jobs.md, "Assess this
 * posting"): the requirements it weighs with where the person stands, the culture screen against
 * the person's own criteria, the signals of its text, and what it says about the role, with the
 * questions to ask when it says too little. Nothing before the first assessment.
 */
export function AssessmentDetails({ job }: { job: JobDetail }) {
  const view = useAssessment(job.id);
  const assessment = view.data?.assessment;
  if (!assessment) return null;
  const result = assessment.result;
  return (
    <Section
      title="Assessment"
      description={`Assessed on ${formatDate(assessment.created_at)}. ${result.verdict}`}
    >
      <AdvancedSection
        title="Requirements, culture, signals and the role"
        pageKey="posting-assessment"
      >
        <Card>
          <CardContent className="flex flex-col gap-8">
            <Requirements result={result} />
            <Culture result={result} />
            <Signals result={result} />
            <RoleClarity result={result} />
          </CardContent>
        </Card>
      </AdvancedSection>
    </Section>
  );
}

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </div>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="max-w-prose text-sm break-words text-muted-foreground">
      <span className="font-medium text-foreground">{label}: </span>
      {children}
    </p>
  );
}

function Requirements({ result }: { result: AssessmentResult }) {
  return (
    <Part title="What the posting asks">
      {result.requirements.length ? (
        <ul aria-label="Requirements" className="flex flex-col divide-y">
          {result.requirements.map((row, index) => (
            <RequirementRow key={index} row={row} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          The agent found no requirement to weigh.
        </p>
      )}
      {result.more_requirements > 0 ? (
        <p className="text-sm text-muted-foreground">
          {`${String(result.more_requirements)} less important ${result.more_requirements === 1 ? "requirement is" : "requirements are"} not listed.`}
        </p>
      ) : null}
    </Part>
  );
}

function RequirementRow({ row }: { row: AssessedRequirement }) {
  return (
    <li
      data-testid="requirement"
      data-match={row.match}
      className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium [overflow-wrap:anywhere]">
          {row.requirement}
        </span>
        <Badge variant="outline">{IMPORTANCE_WORDS[row.importance]}</Badge>
        <Badge variant={row.match === "missing" ? "destructive" : "secondary"}>
          {MATCH_WORDS[row.match]}
        </Badge>
      </div>
      <Line label="Weight">
        {TIER_WORDS[row.tier]}
        {row.signal ? `: ${row.signal}` : ""}
      </Line>
      <Line label="You">{row.profile_evidence}</Line>
      {row.interview_risk ? (
        <Line label="Interview risk">{row.interview_risk}</Line>
      ) : null}
      {row.mitigation ? (
        <Line label="What to say">{row.mitigation}</Line>
      ) : null}
    </li>
  );
}

function Culture({ result }: { result: AssessmentResult }) {
  return (
    <Part title="Your criteria">
      {result.culture.length ? (
        <ul aria-label="Your criteria" className="flex flex-col gap-3">
          {result.culture.map((check, index) => (
            <li key={index} className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">{check.criterion}</span>
                <Badge
                  variant={
                    check.finding === "conflicts" ? "destructive" : "outline"
                  }
                >
                  {FINDING_WORDS[check.finding]}
                </Badge>
              </div>
              <p className="max-w-prose text-sm break-words text-muted-foreground">
                {check.evidence}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          Your profile states none of the criteria this screen reads.
        </p>
      )}
    </Part>
  );
}

function Signals({ result }: { result: AssessmentResult }) {
  return (
    <Part title="What the text shows">
      {result.signals.length ? (
        <ul aria-label="Signals" className="flex flex-col gap-3">
          {result.signals.map((signal, index) => (
            <li key={index} className="flex flex-col gap-1">
              <span className="text-sm font-medium">
                {SIGNAL_WORDS[signal.kind]}
              </span>
              <p className="max-w-prose text-sm break-words text-muted-foreground">
                {signal.note}
              </p>
              <blockquote className="max-w-prose border-s-2 ps-3 text-sm break-words text-muted-foreground italic">
                {signal.quote}
              </blockquote>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          Nothing in the text stood out.
        </p>
      )}
    </Part>
  );
}

function RoleClarity({ result }: { result: AssessmentResult }) {
  const clarity = result.role_clarity;
  if (!clarity.read)
    return (
      <Part title="What it says about the role">
        <p className="text-sm text-muted-foreground">
          This check reads postings in English or French, and found too little
          to read here.
        </p>
      </Part>
    );
  return (
    <Part title="What it says about the role">
      <ul aria-label="The role" className="flex flex-col gap-3">
        {clarity.points.map((point) => (
          <li key={point.id} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{point.label}</span>
              <Badge
                variant={point.status === "present" ? "secondary" : "outline"}
              >
                {CLARITY_WORDS[point.status]}
              </Badge>
            </div>
            {point.evidence[0] ? (
              <blockquote className="max-w-prose border-s-2 ps-3 text-sm break-words text-muted-foreground italic">
                {point.evidence[0]}
              </blockquote>
            ) : null}
          </li>
        ))}
      </ul>
      {clarity.questions.length ? (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-medium">Ask before you commit</h4>
          <ul
            aria-label="Ask before you commit"
            className="flex list-disc flex-col gap-1 ps-5 text-sm"
          >
            {clarity.questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Part>
  );
}
