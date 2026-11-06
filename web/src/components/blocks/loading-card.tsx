import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";

/** The place of a list while it loads: its words beside a spinner, both shown after a short delay. */
export function LoadingCard({ children }: { children: ReactNode }) {
  return (
    <Card
      role="status"
      aria-live="polite"
      className="items-center px-4 py-8 text-sm text-muted-foreground"
    >
      <span className="delayed-appear inline-flex items-center gap-2">
        <Spinner aria-hidden="true" role={undefined} />
        {children}
      </span>
    </Card>
  );
}
