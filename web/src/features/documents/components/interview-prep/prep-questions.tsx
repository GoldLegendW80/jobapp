import { Section } from "@/components/blocks/section";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

import type { PrepContent, Round } from "../../api/interview-prep-api";

const ROUND_NAMES: Record<Round, string> = {
  recruiter: "Recruiter",
  hiring_manager: "Hiring manager",
  peers: "Peers",
};

/**
 * The questions to expect, each with its round and where it comes from: inferred from the
 * posting, or from the hiring panel's review of the CV and letter. Never reported as asked.
 */
export function PrepQuestions({ content }: { content: PrepContent }) {
  return (
    <Section
      title="Questions to expect"
      description="Inferred from the posting and the hiring panel's review of what you sent. Nobody reported them."
    >
      <Card>
        <CardContent>
          <ol className="flex list-decimal flex-col gap-4 pl-5 text-sm">
            {content.expected.map((item, index) => (
              <li key={index} className="flex flex-col gap-1">
                <span className="font-medium">{item.question}</span>
                <span className="flex flex-wrap items-center gap-2 text-muted-foreground">
                  <Badge variant="secondary">{ROUND_NAMES[item.round]}</Badge>
                  <span>
                    {item.basis === "hiring_panel"
                      ? "From the hiring panel"
                      : "From the posting"}
                    {item.why ? `: ${item.why}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </Section>
  );
}

/** The gaps the interview may test, with an honest way to answer each. */
export function PrepGaps({ content }: { content: PrepContent }) {
  if (content.gaps.length === 0) return null;
  return (
    <Section
      title="Gaps and how to answer them"
      description="What the posting asks that your profile does not show, most likely tested first."
    >
      <Card>
        <CardContent>
          <ul className="flex flex-col gap-4 text-sm">
            {content.gaps.map((gap, index) => (
              <li key={index} className="flex flex-col gap-1">
                <span className="font-medium">{gap.gap}</span>
                {gap.answer ? (
                  <span className="text-muted-foreground">{gap.answer}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </Section>
  );
}

/** Questions the person can ask the interviewers. */
export function PrepAsk({ content }: { content: PrepContent }) {
  if (content.ask.length === 0) return null;
  return (
    <Section title="Questions to ask">
      <Card>
        <CardContent>
          <ul className="flex list-disc flex-col gap-2 pl-5 text-sm">
            {content.ask.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </Section>
  );
}
