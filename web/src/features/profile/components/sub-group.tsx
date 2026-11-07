import { useId, type ReactNode } from "react";

/**
 * A group of fields inside a card (a list of skills, the salary of the answers): a small heading,
 * its help and its action on one row, then its fields. Told apart by space, never by a box.
 */
export function SubGroup({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h4 id={id} className="text-sm font-medium">
            {title}
          </h4>
          {description ? (
            <p className="text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
