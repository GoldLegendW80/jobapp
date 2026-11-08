import { Link } from "react-router";

import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";

import { useFollowUps } from "@/features/apply/api/use-followups";
import {
  cadenceLine,
  datesLine,
} from "@/features/apply/helpers/follow-up-words";

/**
 * The posting's follow up on its page (SPEC-apply.md, "Follow ups"): where its cadence stands
 * and its dates, with the way to Applications, where the draft is written and sent. Nothing
 * while the posting is not followed or the person turned follow ups off.
 */
export function FollowUpLine({ jobId }: { jobId: string }) {
  const query = useFollowUps();
  const item = query.data?.items.find((one) => one.posting_id === jobId);
  if (!query.data?.enabled || item === undefined) return null;
  return (
    <Section title="Follow up">
      <Card>
        <CardContent className="flex flex-col gap-1 text-sm">
          <p>{cadenceLine(item, query.data.today)}</p>
          <p className="text-muted-foreground">{datesLine(item)}</p>
          {item.can_draft ? (
            <p>
              <Link to="/applications" className="underline underline-offset-4">
                Write it in Applications
              </Link>
            </p>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}
