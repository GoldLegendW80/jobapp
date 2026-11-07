import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

const PAGE_SIZE = 20;

/** The accounts list of Admin: which ones show, and the way to the page before or after. */
function AccountsPager({ total }: { total: number }) {
  const [offset, setOffset] = useState(0);
  const shown = Math.min(PAGE_SIZE, total - offset);
  const first = offset === 0;
  const last = offset + shown >= total;
  return (
    <Pagination
      aria-label="Accounts pages"
      className="mx-0 max-w-2xl items-center justify-between gap-2 text-sm text-muted-foreground"
    >
      <span>
        {offset + 1} to {offset + shown} of {total}
      </span>
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious asChild>
            <Button
              variant="ghost"
              disabled={first}
              onClick={() => {
                setOffset(Math.max(0, offset - PAGE_SIZE));
              }}
            />
          </PaginationPrevious>
        </PaginationItem>
        <PaginationItem>
          <PaginationNext asChild>
            <Button
              variant="ghost"
              disabled={last}
              onClick={() => {
                setOffset(offset + PAGE_SIZE);
              }}
            />
          </PaginationNext>
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  );
}

const meta = {
  title: "Components/Navigation/Pagination",
  component: Pagination,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The way through a long list shown a page at a time: the accounts and the invite links of Admin. It says which items show out of how many. Its links are links when the page is in the address; asChild makes them buttons when the page lives in the component.",
      },
    },
  },
} satisfies Meta<typeof Pagination>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Accounts, 20 at a time: where you are, Previous and Next. Click Next. */
export const Typical: Story = {
  render: () => <AccountsPager total={57} />,
};

/** Page numbers, the current one outlined, an ellipsis for the pages between. */
export const WithPageNumbers: Story = {
  render: () => (
    <Pagination aria-label="Invite links pages">
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious href="#page-2" />
        </PaginationItem>
        <PaginationItem>
          <PaginationLink href="#page-1">1</PaginationLink>
        </PaginationItem>
        <PaginationItem>
          <PaginationLink href="#page-2">2</PaginationLink>
        </PaginationItem>
        <PaginationItem>
          <PaginationLink href="#page-3" isActive>
            3
          </PaginationLink>
        </PaginationItem>
        <PaginationItem>
          <PaginationEllipsis />
        </PaginationItem>
        <PaginationItem>
          <PaginationLink href="#page-9">9</PaginationLink>
        </PaginationItem>
        <PaginationItem>
          <PaginationNext href="#page-4" />
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  ),
};

/** On the first page, Previous is disabled; on the last, Next is. */
export const FirstAndLastPage: Story = {
  render: () => (
    <div className="flex flex-col gap-4">
      <AccountsPager total={45} />
      <Pagination aria-label="Last page">
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious href="#page-2" />
          </PaginationItem>
          <PaginationItem>
            <PaginationNext aria-disabled="true" tabIndex={-1} />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  ),
};

/** On a phone, Previous and Next keep their arrows; the words stay for screen readers. */
export const OnAPhone: Story = {
  render: () => (
    <div className="max-w-sm">
      <AccountsPager total={57} />
    </div>
  ),
};
