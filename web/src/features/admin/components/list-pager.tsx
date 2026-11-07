import { Button } from "@/components/ui/button";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

/** Which items a table shows out of how many, and the way to the page before or after. */
export function ListPager({
  label,
  pageSize,
  offset,
  shown,
  total,
  onOffset,
}: {
  /** The navigation's accessible name, "Accounts pages". */
  label: string;
  pageSize: number;
  offset: number;
  shown: number;
  total: number;
  onOffset: (offset: number) => void;
}) {
  return (
    <Pagination
      aria-label={label}
      className="mx-0 items-center justify-between gap-2 text-sm text-muted-foreground"
    >
      <span>
        {offset + 1} to {offset + shown} of {total}
      </span>
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious asChild>
            <Button
              variant="ghost"
              disabled={offset === 0}
              onClick={() => {
                onOffset(Math.max(0, offset - pageSize));
              }}
            />
          </PaginationPrevious>
        </PaginationItem>
        <PaginationItem>
          <PaginationNext asChild>
            <Button
              variant="ghost"
              disabled={offset + shown >= total}
              onClick={() => {
                onOffset(offset + pageSize);
              }}
            />
          </PaginationNext>
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  );
}
