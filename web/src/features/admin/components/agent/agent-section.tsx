import type { ReactNode } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** One card of the agent page, a region named by its title. */
export function AgentSection({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <Card role="region" aria-labelledby={`${id}-title`} className="min-w-0">
      <CardHeader>
        <CardTitle>
          <h2 id={`${id}-title`} className="text-lg tracking-tight">
            {title}
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex min-w-0 flex-col gap-3">
        {children}
      </CardContent>
    </Card>
  );
}
