import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";

import { USERS_PAGE_SIZE } from "../../api/admin-api";

/** Which accounts the table shows out of how many, and the way to the page before or after. */
export function UsersPager({
  offset,
  shown,
  total,
  onOffset,
}: {
  offset: number;
  shown: number;
  total: number;
  onOffset: (offset: number) => void;
}) {
  return (
    <nav
      aria-label="Accounts pages"
      className="flex items-center justify-between gap-2 text-sm text-muted-foreground"
    >
      <span>
        {offset + 1} to {offset + shown} of {total}
      </span>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => {
            onOffset(Math.max(0, offset - USERS_PAGE_SIZE));
          }}
        >
          <ChevronLeft aria-hidden="true" />
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + shown >= total}
          onClick={() => {
            onOffset(offset + USERS_PAGE_SIZE);
          }}
        >
          Next
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}
