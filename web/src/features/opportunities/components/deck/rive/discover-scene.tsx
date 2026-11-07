/**
 * The Rive runtime and the Discover scenes, in a chunk of their own that only deck-scene.tsx
 * loads, lazily: no other page downloads it, and the deck works before it arrives. The scenes'
 * source is web/rive/discover/ (RML), built with `rive web/rive/discover --once` into
 * features/opportunities/assets/discover.riv.
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

import riveUrl from "@/features/opportunities/assets/discover.riv?url";
import type { DeckSignals } from "@/features/opportunities/hooks/deck/use-deck-signals";

import type { SceneName } from "../deck-scene";

// The runtime is served by the app itself: no request leaves for a CDN.
RuntimeLoader.setWasmUrl(wasmUrl);
RuntimeLoader.setWasmFallbackUrl(null);

const LAYOUT = new Layout({ fit: Fit.Contain, alignment: Alignment.Center });

/**
 * One artboard of discover.riv, playing its state machine (named as the artboard). Reaction reads
 * `signals`: the lean sets its `lean` number (-100 Pass to 100 Like), a burst fires its `like` or
 * `pass` trigger. Done fires `celebrate` each time `replay` grows.
 */
export default function DiscoverScene({
  scene,
  signals,
  replay = 0,
  onReady,
  onFail,
  className,
}: {
  scene: SceneName;
  signals?: DeckSignals;
  replay?: number;
  onReady: () => void;
  onFail: () => void;
  className?: string;
}) {
  const { rive, RiveComponent } = useRive({
    src: riveUrl,
    artboard: scene,
    stateMachine: scene,
    autoplay: true,
    autoBind: scene === "Reaction" || scene === "Done",
    layout: LAYOUT,
    enableRiveAssetCDN: false,
    shouldDisableRiveListeners: true,
    useOffscreenRenderer: false,
    onLoad: onReady,
    onLoadError: onFail,
  });

  useEffect(() => {
    if (!rive || !signals) return;
    const model = rive.viewModelInstance;
    const lean = model?.number("lean");
    const triggers = {
      like: model?.trigger("like"),
      pass: model?.trigger("pass"),
    };
    const offLean = signals.onLean((value) => {
      if (lean) lean.value = Math.round(value * 100);
    });
    const offBurst = signals.onBurst((kind) => {
      triggers[kind]?.trigger();
    });
    return () => {
      offLean();
      offBurst();
    };
  }, [rive, signals]);

  useEffect(() => {
    if (!rive || replay === 0) return;
    rive.viewModelInstance?.trigger("celebrate")?.trigger();
  }, [rive, replay]);

  return <RiveComponent className={className} />;
}
