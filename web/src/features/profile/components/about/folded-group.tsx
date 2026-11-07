import type { ReactNode } from "react";

import {
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";

/**
 * A group of fields of About you without a model key (a list of skills, a group of answers),
 * folded under its title and what it holds now (`summary`): the person opens the one they need.
 * It is an item of an Accordion.
 */
export function FoldedGroup({
  value,
  title,
  summary,
  description,
  action,
  children,
}: {
  value: string;
  title: string;
  summary: string;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <AccordionItem value={value}>
      <AccordionTrigger level={4} aria-label={`${title}, ${summary}`}>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span>{title}</span>
          <span className="font-normal text-muted-foreground">{summary}</span>
        </span>
      </AccordionTrigger>
      {/* Room for the focus ring of the fields, which the folding would cut. */}
      <AccordionContent className="flex flex-col gap-4 px-1">
        {description || action ? (
          <div className="flex flex-wrap items-start justify-between gap-2">
            {description ? (
              <p className="text-muted-foreground">{description}</p>
            ) : null}
            {action}
          </div>
        ) : null}
        {children}
      </AccordionContent>
    </AccordionItem>
  );
}
