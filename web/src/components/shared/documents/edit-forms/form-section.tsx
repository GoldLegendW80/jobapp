import { useId, type ReactNode } from "react";

/** A titled part of the Edit view's form, named by its heading. */
export function FormSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-4 rounded-lg border p-3 sm:p-4"
    >
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}
