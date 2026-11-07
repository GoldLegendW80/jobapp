import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

import type { DeckSignals } from "@/features/opportunities/hooks/deck/use-deck-signals";

/** The artboards of discover.riv (web/rive/discover/), each playing the state machine of its name. */
export type SceneName = "Reaction" | "Hint" | "Done" | "Empty" | "Loading";

const DiscoverScene = lazy(() => import("./rive/discover-scene"));

/** Whether this browser can draw Rive: WebAssembly and a 2D canvas (jsdom has neither canvas). */
function riveSupported(): boolean {
  return (
    typeof WebAssembly === "object" &&
    typeof CanvasRenderingContext2D === "function"
  );
}

/** Runs `work` once the browser is idle, so the deck is drawn and usable first. */
function whenIdle(work: () => void): () => void {
  if (typeof window.requestIdleCallback === "function") {
    const id = window.requestIdleCallback(work, { timeout: 1500 });
    return () => {
      window.cancelIdleCallback(id);
    };
  }
  const id = window.setTimeout(work, 250);
  return () => {
    window.clearTimeout(id);
  };
}

/** A scene that could not load (its chunk, the runtime, the file) leaves its fallback in place. */
class SceneBoundary extends Component<
  { onError: () => void; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onError();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/**
 * A Rive scene of Discover, decoration only (hidden from screen readers): the runtime loads once
 * the browser is idle, never with reduced motion, and the scene fades in over `fallback` once it
 * has drawn. When it cannot load, `fallback` stays: the page never depends on it.
 */
export function DeckScene({
  scene,
  motion,
  signals,
  replay,
  fallback,
  className,
}: {
  scene: SceneName;
  motion: boolean;
  signals?: DeckSignals;
  replay?: number;
  fallback?: ReactNode;
  className?: string;
}) {
  const [start, setStart] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const live = motion && start && !failed;

  useEffect(() => {
    if (!motion || !riveSupported()) return;
    return whenIdle(() => {
      setStart(true);
    });
  }, [motion]);

  const onReady = useCallback(() => {
    setReady(true);
  }, []);
  const onFail = useCallback(() => {
    setFailed(true);
  }, []);

  return (
    <div
      aria-hidden="true"
      data-scene={scene}
      data-state={live ? (ready ? "playing" : "loading") : "still"}
      className={cn("pointer-events-none relative", className)}
    >
      {fallback && !(live && ready) ? (
        <div className="absolute inset-0 grid place-items-center">
          {fallback}
        </div>
      ) : null}
      {live ? (
        <SceneBoundary onError={onFail}>
          <Suspense fallback={null}>
            <DiscoverScene
              scene={scene}
              signals={signals}
              replay={replay}
              onReady={onReady}
              onFail={onFail}
              className={cn(
                "absolute inset-0 size-full transition-opacity duration-300 ease-out",
                ready ? "opacity-100" : "opacity-0",
              )}
            />
          </Suspense>
        </SceneBoundary>
      ) : null}
    </div>
  );
}
