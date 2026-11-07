import { useEffect, useRef } from "react";

import type { DeckSignals } from "@/features/opportunities/hooks/deck/use-deck-signals";

/**
 * The deck's backdrop, edge to edge: a warm wash of the brand colour that drifts slowly, and a
 * tint that follows the card on top, orange toward Like, red toward Pass. With reduced motion it
 * holds still.
 */
export function DeckBackdrop({
  signals,
  motion,
}: {
  signals: DeckSignals;
  motion: boolean;
}) {
  const like = useRef<HTMLDivElement>(null);
  const pass = useRef<HTMLDivElement>(null);
  const glow = useRef<HTMLDivElement>(null);

  useEffect(
    () =>
      signals.onLean((value) => {
        if (like.current)
          like.current.style.opacity = String(Math.max(0, value));
        if (pass.current)
          pass.current.style.opacity = String(Math.max(0, -value));
      }),
    [signals],
  );

  useEffect(() => {
    const element = glow.current;
    if (!motion || typeof element?.animate !== "function") return;
    const drift = element.animate(
      [
        { transform: "translate3d(-6%, -4%, 0) scale(1)" },
        { transform: "translate3d(5%, 3%, 0) scale(1.12)" },
      ],
      {
        duration: 18000,
        iterations: Infinity,
        direction: "alternate",
        easing: "ease-in-out",
      },
    );
    return () => {
      drift.cancel();
    };
  }, [motion]);

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
    >
      <div className="absolute inset-0 bg-[linear-gradient(180deg,color-mix(in_oklch,var(--highlight)_55%,var(--background))_0%,var(--background)_70%)]" />
      <div
        ref={glow}
        className="absolute -inset-[20%] bg-[radial-gradient(40%_35%_at_30%_25%,color-mix(in_oklch,var(--brand)_22%,transparent),transparent_70%),radial-gradient(35%_30%_at_75%_70%,color-mix(in_oklch,var(--brand)_14%,transparent),transparent_70%)]"
      />
      <div
        ref={like}
        style={{ opacity: 0 }}
        className="absolute inset-0 bg-[radial-gradient(60%_80%_at_100%_45%,color-mix(in_oklch,var(--brand)_30%,transparent),transparent_70%)]"
      />
      <div
        ref={pass}
        style={{ opacity: 0 }}
        className="absolute inset-0 bg-[radial-gradient(60%_80%_at_0%_45%,color-mix(in_oklch,var(--destructive)_22%,transparent),transparent_70%)]"
      />
    </div>
  );
}
