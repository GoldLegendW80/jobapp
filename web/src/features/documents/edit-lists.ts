import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import {
  listAt,
  moved,
  pathKey,
  type Draft,
  type Note,
  type Path,
  type SoftLimits,
} from "./edit-content";

/** What every field of the Edit view reads and writes. */
export interface Form {
  /** The prefix of the ids of this form's inputs. */
  id: string;
  draft: Draft;
  set: (path: Path, value: unknown) => void;
  notes: Map<string, Note[]>;
  /** What the api refused at the last save, by path. */
  errors: Map<string, string>;
  limits: SoftLimits | undefined;
}

export const inputId = (form: Form, path: Path): string =>
  `${form.id}-${pathKey(path).replace(/\./g, "-")}`;

/** Puts the focus on an element by id after the next render, the one that creates or moves it. */
export function useFocusAfter(): (id: string) => void {
  const target = useRef<string>(undefined);
  useEffect(() => {
    if (target.current === undefined) return;
    document.getElementById(target.current)?.focus();
    target.current = undefined;
  });
  return (id) => {
    target.current = id;
  };
}

/**
 * Moves and removals of a list, with the focus kept on what moved and the move said aloud.
 * Alt with an arrow key moves the entry whose field has the focus.
 */
export function useListMoves(
  form: Form,
  path: Path,
  noun: string,
): {
  said: string;
  move: (from: number, to: number, focus: "up" | "down" | "field") => void;
  remove: (index: number) => void;
  keys: (index: number) => (event: KeyboardEvent) => void;
  focus: (id: string) => void;
} {
  const focus = useFocusAfter();
  const [said, setSaid] = useState("");
  const items = listAt(form.draft, path);
  const base = inputId(form, path);
  const move = (from: number, to: number, where: "up" | "down" | "field") => {
    if (to < 0 || to >= items.length) return;
    form.set(path, moved(items, from, to));
    setSaid(`${noun} ${String(from + 1)} moved to place ${String(to + 1)}`);
    const stuck =
      (where === "up" && to === 0) ||
      (where === "down" && to === items.length - 1);
    focus(
      where === "field" || stuck
        ? `${base}-${String(to)}`
        : `${base}-${String(to)}-${where}`,
    );
  };
  return {
    said,
    move,
    remove: (index) => {
      form.set(
        path,
        items.filter((_, i) => i !== index),
      );
      setSaid(`${noun} ${String(index + 1)} removed`);
      focus(`${base}-add`);
    },
    keys: (index) => (event) => {
      if (!event.altKey) return;
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        move(index, event.key === "ArrowUp" ? index - 1 : index + 1, "field");
      }
    },
    focus,
  };
}
