import type { Citation, ProposalItem } from "../../api/proposals-api";
import { CitationList } from "./citation-list";
import { ProfileValue } from "./profile-value";

/** What the profile holds now beside what the proposal would make of it, and its sources. */
export function ProposalComparison({
  item,
  title,
  onOpen,
}: {
  item: ProposalItem;
  title: string;
  onOpen: (citation: Citation) => void;
}) {
  const removal = item.action === "remove";
  return (
    <>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <p className="text-xs text-muted-foreground">In your profile now</p>
          <ProfileValue value={item.before} label={`${title}, now`} />
        </div>
        <div>
          <p className="text-xs text-muted-foreground">
            {removal ? "After: removed" : "Proposed"}
          </p>
          {removal ? null : (
            <ProfileValue
              value={item.status === "pending" ? item.after : item.value}
              label={`${title}, proposed`}
            />
          )}
        </div>
      </div>
      <CitationList
        citations={item.citations}
        label={`Sources of ${title}`}
        onOpen={onOpen}
      />
    </>
  );
}
