/**
 * The Rive runtime and the scene of "Building your profile", in a chunk of its own that only
 * building-animation.tsx loads, lazily. Source: web/rive/building/ (RML), built with
 * `rive rive/building --once` from web/ into features/onboarding/assets/building.riv.
 */
import wasmUrl from "@rive-app/canvas/rive.wasm?url";
import {
  Alignment,
  Fit,
  Layout,
  RuntimeLoader,
  useRive,
} from "@rive-app/react-canvas";
import { useEffect } from "react";

import riveUrl from "@/features/onboarding/assets/building.riv?url";

// The runtime is served by the app itself: no request leaves for a CDN.
RuntimeLoader.setWasmUrl(wasmUrl);
RuntimeLoader.setWasmFallbackUrl(null);

const LAYOUT = new Layout({ fit: Fit.Contain, alignment: Alignment.Center });

/** The `Building` artboard: `progress` (0 to 100) fills the card, `finish` plays the finale. */
export default function BuildingScene({
  progress,
  finished,
  onReady,
  onFail,
  className,
}: {
  progress: number;
  finished: boolean;
  onReady: () => void;
  onFail: () => void;
  className?: string;
}) {
  const { rive, RiveComponent } = useRive({
    src: riveUrl,
    artboard: "Building",
    stateMachine: "Building",
    autoplay: true,
    autoBind: true,
    layout: LAYOUT,
    enableRiveAssetCDN: false,
    shouldDisableRiveListeners: true,
    useOffscreenRenderer: false,
    onLoad: onReady,
    onLoadError: onFail,
  });

  useEffect(() => {
    const value = rive?.viewModelInstance?.number("progress");
    if (value) value.value = progress;
  }, [rive, progress]);

  useEffect(() => {
    if (finished) rive?.viewModelInstance?.trigger("finish")?.trigger();
  }, [rive, finished]);

  return <RiveComponent className={className} />;
}
