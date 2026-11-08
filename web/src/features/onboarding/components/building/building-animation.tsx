import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { useMediaQuery } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";

const BuildingScene = lazy(() => import("./building-scene"));

/** Whether this browser can draw Rive: WebAssembly and a 2D canvas (jsdom has neither canvas). */
function riveSupported(): boolean {
  return (
    typeof WebAssembly === "object" &&
    typeof CanvasRenderingContext2D === "function"
  );
}

/** A scene that could not load (its chunk, the runtime, the file) leaves nothing in its place. */
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
 * The animation of "Building your profile", decoration only (hidden from screen readers): the
 * runtime loads once the browser is idle, never with reduced motion, and fades in once drawn.
 * Where it does not play (reduced motion, no Rive, a failed load) it takes no space: the step's
 * own words and bar say where it stands.
 */
export function BuildingAnimation({
  progress,
  finished,
  className,
}: {
  progress: number;
  finished: boolean;
  className?: string;
}) {
  const motion = !useMediaQuery("(prefers-reduced-motion: reduce)");
  const [supported] = useState(riveSupported);
  const [start, setStart] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const plays = motion && supported && !failed;
  const live = plays && start;

  useEffect(() => {
    if (!motion || !supported) return;
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(
        () => {
          setStart(true);
        },
        { timeout: 1000 },
      );
      return () => {
        window.cancelIdleCallback(id);
      };
    }
    const id = window.setTimeout(() => {
      setStart(true);
    }, 200);
    return () => {
      window.clearTimeout(id);
    };
  }, [motion, supported]);

  const onReady = useCallback(() => {
    setReady(true);
  }, []);
  const onFail = useCallback(() => {
    setFailed(true);
  }, []);

  if (!plays) return null;
  return (
    <div
      aria-hidden="true"
      data-state={live ? (ready ? "playing" : "loading") : "still"}
      className={cn("pointer-events-none relative", className)}
    >
      {live ? (
        <SceneBoundary onError={onFail}>
          <Suspense fallback={null}>
            <BuildingScene
              progress={progress}
              finished={finished}
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
