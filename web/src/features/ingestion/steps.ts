/**
 * The three steps of /profile/fill (SPEC-ingestion.md, Frontend). The page shell (fill-page.tsx)
 * owns the list, the navigation and the address; each step is one file that renders its own
 * panel and never knows about the others, so the tasks that fill them in do not meet.
 */
export const STEP_IDS = ["prompt", "documents", "review"] as const;
export type StepId = (typeof STEP_IDS)[number];

export const isStepId = (value: string | null): value is StepId =>
  value !== null && (STEP_IDS as readonly string[]).includes(value);

/** What the shell hands every step. */
export interface StepProps {
  /** Open another step (one that is not ready yet is ignored). */
  goTo: (step: StepId) => void;
}
