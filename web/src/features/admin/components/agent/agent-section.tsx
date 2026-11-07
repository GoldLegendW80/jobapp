import type { ReactNode } from "react";

import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";

/**
 * One group of the agent page: its name and purpose on the left, its content on the right, on a
 * card unless `surface` is "own" (a table, or rows that bring their own card).
 */
export function AgentSection({
  title,
  description,
  surface = "card",
  children,
}: {
  title: string;
  description?: ReactNode;
  surface?: "card" | "own";
  children: ReactNode;
}) {
  return (
    <Section layout="side" title={title} description={description}>
      {surface === "card" ? (
        <Card>
          <CardContent className="flex min-w-0 flex-col gap-4">
            {children}
          </CardContent>
        </Card>
      ) : (
        children
      )}
    </Section>
  );
}
