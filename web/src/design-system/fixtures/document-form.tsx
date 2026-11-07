import { useId, useState, type ReactNode } from "react";

import {
  setAt,
  type Draft,
  type Note,
  type SoftLimits,
} from "@/features/documents/helpers/edit-content";
import type { Form } from "@/features/documents/helpers/edit-form";
import { pageFacts } from "@/features/documents/helpers/page-facts";

import { camilleProfile } from "./camille-profile";

/** The template's suggested lengths, as Classic serves them. */
export const CLASSIC_LIMITS: Record<"cv" | "letter", SoftLimits> = {
  cv: {
    headline: 90,
    summary: 330,
    highlight: 60,
    competency: 40,
    main_bullet: 190,
    bullet: 100,
    paragraph: null,
  },
  letter: { headline: 90, paragraph: 900, summary: null },
};

/** What Camille's page prints from her profile (camille-profile.ts). */
export const CAMILLE_FACTS = pageFacts(camilleProfile(), {});

/**
 * A draft held in the story, with the Form the document's fields read and write: what the edit
 * panel gives them, without an api behind (nothing is saved).
 */
export function DocumentForm({
  initial,
  notes = new Map<string, Note[]>(),
  errors = new Map<string, string>(),
  kind = "cv",
  children,
}: {
  initial: Draft;
  notes?: Map<string, Note[]>;
  errors?: Map<string, string>;
  kind?: "cv" | "letter";
  children: (form: Form) => ReactNode;
}) {
  const id = useId();
  const [draft, setDraft] = useState(initial);
  const form: Form = {
    id,
    draft,
    set: (path, value) => {
      setDraft((current) => setAt(current, path, value) as Draft);
    },
    notes,
    errors,
    limits: CLASSIC_LIMITS[kind],
  };
  return <>{children(form)}</>;
}
