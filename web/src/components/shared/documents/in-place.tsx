import {
  Fragment,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ElementType,
  type MouseEvent,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { getAt, type Path } from "@/features/documents/helpers/edit-content";
import { PageForm } from "./page-form";

const DIALOG = '[role="dialog"],[role="alertdialog"]';

const inDialog = (node: EventTarget | null) =>
  node instanceof Element && node.closest(DIALOG) !== null;

/** The text under the pointer that names the field to edit (`data-edit`), when a click opened it. */
function pointedAt(event: MouseEvent): string | undefined {
  if (event.detail === 0 || typeof document.elementsFromPoint !== "function")
    return undefined;
  for (const one of document.elementsFromPoint(event.clientX, event.clientY)) {
    if (one instanceof HTMLElement && one.dataset.edit !== undefined)
      return one.dataset.edit;
  }
  return undefined;
}

/** The field labelled `label` in `root`, else its first field, else its first button. */
function fieldIn(root: HTMLElement, label: string | undefined) {
  const fields = [
    ...root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
      "input, textarea, select",
    ),
  ];
  const named =
    label === undefined
      ? undefined
      : fields.find((one) => one.labels?.[0]?.textContent === label);
  return named ?? fields[0] ?? root.querySelector("button");
}

/**
 * A part of the document's page that is edited where it is read. Read, it is the page's own text
 * with a button over it ("Edit the summary"): a click or Enter puts `editor` in its place, with the
 * focus in the field that was clicked (a text marked `data-edit` with its field's label). Escape
 * puts back what `paths` held when it opened; Done, a click outside or the focus leaving keep what
 * was typed, which the form saves by itself. `problems` (notes, refusals) show under the text
 * while it is closed. Without a PageForm around it, it is only the text.
 *
 * With `as`, the part is an element of a template's page (`as` and `className` are the template's
 * own): what it adds (`data-sheet-ui`) stays out of the template's style and of its structure, and
 * its problems ring the part instead of showing under it (the sheet lists them above the page).
 */
export function InPlace({
  label,
  paths,
  editor,
  problems = [],
  as,
  className,
  children,
}: {
  label: string;
  paths: Path[];
  editor: ReactNode;
  problems?: string[];
  as?: ElementType;
  className?: string;
  children: ReactNode;
}) {
  const form = useContext(PageForm);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const before = useRef<unknown[]>([]);
  const target = useRef<string | undefined>(undefined);
  const refocus = useRef(false);

  const close = (back: boolean) => {
    refocus.current = back;
    setOpen(false);
  };

  useLayoutEffect(() => {
    if (open && box.current) {
      const field = fieldIn(box.current, target.current);
      field?.focus();
      if (
        field instanceof HTMLInputElement ||
        field instanceof HTMLTextAreaElement
      ) {
        const end = field.value.length;
        field.setSelectionRange(end, end);
      }
    } else if (!open && refocus.current) {
      refocus.current = false;
      button.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const node = event.target;
      if (!(node instanceof Node) || box.current?.contains(node)) return;
      if (inDialog(node)) return;
      refocus.current = false;
      setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => {
      document.removeEventListener("pointerdown", outside);
    };
  }, [open]);

  const Part = as ?? "div";
  if (form === null) return <Part className={className}>{children}</Part>;

  if (open)
    return (
      <div
        ref={box}
        role="group"
        aria-label={`Editing ${label}`}
        data-sheet-ui=""
        className={cn(
          "relative z-10 -mx-2 flex flex-col gap-3 rounded-lg bg-card p-3 font-sans text-sm text-card-foreground shadow-md ring-2 ring-ring/50",
          // Inside a template's page the box inherits its type: it starts again from the app's.
          as !== undefined &&
            "my-1 min-w-[min(18rem,100cqw)] text-left leading-normal font-normal tracking-normal whitespace-normal not-italic normal-case [font-variant:normal] [text-indent:0] [word-spacing:normal]",
          as === undefined && className,
        )}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || event.defaultPrevented) return;
          event.preventDefault();
          paths.forEach((path, index) => {
            const was = before.current[index];
            if (JSON.stringify(getAt(form.draft, path)) !== JSON.stringify(was))
              form.set(path, was);
          });
          close(true);
        }}
        onBlur={(event) => {
          const next = event.relatedTarget;
          if (next === null || box.current?.contains(next) || inDialog(next))
            return;
          close(false);
        }}
      >
        <Fragment key={form.round ?? 0}>{editor}</Fragment>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            size="sm"
            onClick={() => {
              close(true);
            }}
          >
            Done
          </Button>
          <span className="text-xs text-muted-foreground">
            Escape puts back the text it had.
          </span>
        </div>
      </div>
    );

  const edit = (
    <Button
      ref={button}
      type="button"
      variant="ghost"
      aria-label={`Edit ${label}`}
      aria-description={
        as !== undefined && problems.length > 0 ? problems.join(" ") : undefined
      }
      data-sheet-ui=""
      className={cn(
        "absolute -inset-1 h-auto cursor-text rounded-md p-0 hover:bg-ring/5 hover:ring-1 hover:ring-ring/60 focus-visible:ring-[3px] focus-visible:ring-ring/60",
        // Over a template's own boxes (a marker drawn as a box), the button stays on top.
        as !== undefined && "z-[1]",
        as !== undefined &&
          problems.length > 0 &&
          "ring-2 ring-warning/70 hover:ring-2 hover:ring-warning",
      )}
      onClick={(event) => {
        before.current = paths.map((path) => getAt(form.draft, path));
        target.current = pointedAt(event);
        setOpen(true);
      }}
    />
  );

  if (as !== undefined)
    return (
      <Part className={className} style={{ position: "relative" }}>
        {edit}
        {children}
      </Part>
    );

  return (
    <div className={cn("relative", className)}>
      {children}
      {problems.length === 0 ? null : (
        <ul className="mt-1 flex flex-col gap-0.5 font-sans text-xs text-warning">
          {problems.map((one, index) => (
            <li key={index}>{one}</li>
          ))}
        </ul>
      )}
      {edit}
    </div>
  );
}
