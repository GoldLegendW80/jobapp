import { useId, type ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";

/** A titled part of the document's form, on a card of its own and named by its heading. */
export function FormSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <Card>
        <CardContent className="flex flex-col gap-4">
          <h3 id={id} className="text-sm font-medium">
            {title}
          </h3>
          {children}
        </CardContent>
      </Card>
    </section>
  );
}
