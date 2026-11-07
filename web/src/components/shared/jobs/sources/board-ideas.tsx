import { Plus } from "lucide-react";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Button } from "@/components/ui/button";
import type { CompanyBoard } from "@/features/profile/api/profile-api";

import { ATS_NAMES, boardKey } from "@/features/jobs/api/boards-api";
import { useBoardIdeas } from "@/features/jobs/api/use-board-ideas";

interface IdeasProps {
  boards: readonly CompanyBoard[] | null;
  disabled: boolean;
  onAdd: (boards: CompanyBoard[]) => void;
}

/** More company job boards, folded away: the starter list and the boards of the person's own
 * postings, each one click. Nothing is added by itself, and nothing is asked before it opens. */
export function BoardIdeas(props: IdeasProps) {
  return (
    <AdvancedSection title="More company job boards">
      <BoardIdeasList {...props} />
    </AdvancedSection>
  );
}

function BoardIdeasList({ boards, disabled, onAdd }: IdeasProps) {
  const { pending, fromPostings, popular } = useBoardIdeas(boards);
  if (pending)
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading ideas…
      </p>
    );
  if (!fromPostings.length && !popular.length)
    return (
      <p className="text-sm text-muted-foreground">
        No more ideas right now. Paste the address of any company job board
        above.
      </p>
    );
  return (
    <>
      {fromPostings.length ? (
        <IdeaChips
          title="From your postings"
          boards={fromPostings}
          disabled={disabled}
          onAdd={onAdd}
        />
      ) : null}
      {popular.length ? (
        <IdeaChips
          title="Popular boards"
          boards={popular}
          disabled={disabled}
          onAdd={onAdd}
          addAll
        />
      ) : null}
    </>
  );
}

function IdeaChips({
  title,
  boards,
  disabled,
  onAdd,
  addAll = false,
}: {
  title: string;
  boards: CompanyBoard[];
  disabled: boolean;
  onAdd: (boards: CompanyBoard[]) => void;
  addAll?: boolean;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{title}</p>
        {addAll ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => {
              onAdd(boards);
            }}
          >
            Add all {boards.length}
          </Button>
        ) : null}
      </div>
      <ul aria-label={title} className="flex flex-wrap gap-2">
        {boards.map((board) => (
          <li key={boardKey(board)}>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="rounded-full"
              disabled={disabled}
              aria-label={`Add ${board.company} (${ATS_NAMES[board.ats]})`}
              onClick={() => {
                onAdd([board]);
              }}
            >
              <Plus aria-hidden="true" />
              {board.company}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
