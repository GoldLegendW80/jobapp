import * as React from "react";
import { cn } from "@/lib/utils";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  MoreHorizontalIcon,
} from "lucide-react";
import { Slot } from "radix-ui";

import { buttonVariants, type Button } from "@/components/ui/button";

function Pagination({ className, ...props }: React.ComponentProps<"nav">) {
  return (
    <nav
      role="navigation"
      aria-label="Pages"
      data-slot="pagination"
      className={cn("mx-auto flex w-full justify-center", className)}
      {...props}
    />
  );
}

function PaginationContent({
  className,
  ...props
}: React.ComponentProps<"ul">) {
  return (
    <ul
      data-slot="pagination-content"
      className={cn("flex flex-row items-center gap-1", className)}
      {...props}
    />
  );
}

function PaginationItem({ ...props }: React.ComponentProps<"li">) {
  return <li data-slot="pagination-item" {...props} />;
}

type PaginationLinkProps = {
  isActive?: boolean;
  /** A router Link, or a button when the page lives in the component's state. */
  asChild?: boolean;
} & Pick<React.ComponentProps<typeof Button>, "size"> &
  React.ComponentProps<"a">;

function PaginationLink({
  className,
  isActive,
  size = "icon",
  asChild = false,
  ...props
}: PaginationLinkProps) {
  const Comp = asChild ? Slot.Root : "a";
  return (
    <Comp
      aria-current={isActive ? "page" : undefined}
      data-slot="pagination-link"
      data-active={isActive}
      className={cn(
        buttonVariants({
          variant: isActive ? "outline" : "ghost",
          size,
        }),
        "aria-disabled:pointer-events-none aria-disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

// The visible word is the accessible name; on a phone it stays for screen readers only.
function PaginationPrevious({
  className,
  children,
  ...props
}: React.ComponentProps<typeof PaginationLink>) {
  return (
    <PaginationLink
      size="default"
      className={cn("gap-1 px-3 sm:pl-3", className)}
      {...props}
    >
      <ChevronLeftIcon aria-hidden="true" />
      <Slot.Slottable>{children}</Slot.Slottable>
      <span className="max-sm:sr-only">Previous</span>
    </PaginationLink>
  );
}

function PaginationNext({
  className,
  children,
  ...props
}: React.ComponentProps<typeof PaginationLink>) {
  return (
    <PaginationLink
      size="default"
      className={cn("gap-1 px-3 sm:pr-3", className)}
      {...props}
    >
      <span className="max-sm:sr-only">Next</span>
      <Slot.Slottable>{children}</Slot.Slottable>
      <ChevronRightIcon aria-hidden="true" />
    </PaginationLink>
  );
}

function PaginationEllipsis({
  className,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      aria-hidden
      data-slot="pagination-ellipsis"
      className={cn("flex size-10 items-center justify-center", className)}
      {...props}
    >
      <MoreHorizontalIcon className="size-4" />
    </span>
  );
}

export {
  Pagination,
  PaginationContent,
  PaginationLink,
  PaginationItem,
  PaginationPrevious,
  PaginationNext,
  PaginationEllipsis,
};
