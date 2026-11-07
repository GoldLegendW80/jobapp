import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { cn } from "@/lib/utils";

/**
 * In place of a list or a view with nothing in it: an icon, what is missing, what to do, and the
 * action that does it. On its own white surface, or flat inside one (`flat`).
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  actions,
  flat = false,
}: {
  icon: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  flat?: boolean;
}) {
  return (
    <Empty
      className={cn(
        "gap-4 px-4 py-12 md:px-6 md:py-12",
        !flat && "rounded-xl bg-card",
      )}
    >
      <EmptyHeader className="gap-2">
        <EmptyMedia variant="icon" className="mb-1">
          <Icon aria-hidden="true" />
        </EmptyMedia>
        <EmptyTitle className="text-base tracking-normal">{title}</EmptyTitle>
        {description ? (
          <EmptyDescription>{description}</EmptyDescription>
        ) : null}
      </EmptyHeader>
      {actions ? (
        <EmptyContent className="flex-row flex-wrap justify-center">
          {actions}
        </EmptyContent>
      ) : null}
    </Empty>
  );
}
