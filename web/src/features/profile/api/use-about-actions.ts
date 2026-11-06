/** The calls on "About you" beside its autosave: Done on a part, and the actions on a reading or
 * a part (Apply, Undo, Not now, Rewrite from my profile, Add them). */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/lib/api";

import {
  aboutKey,
  actOnReading,
  doneAbout,
  rewriteAbout,
  staleAbout,
  type About,
} from "./about-api";
import { profileKey } from "./profile-api";
import { shownPart } from "../helpers/about-parts";

/** A button on the last reading (Apply, Undo, Not now) or on the parts (Rewrite, Add them). */
export type AboutAction =
  | { name: "apply" | "undo" | "discard" }
  | { name: "rewrite"; heading: string | null; version: number };

function send(action: AboutAction): Promise<About> {
  return action.name === "rewrite"
    ? rewriteAbout(action.heading, action.version)
    : actOnReading(action.name);
}

export function useAboutActions(flush: () => Promise<About | undefined>) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);

  /** One action on the document, then the profile it changed is loaded again. */
  const runAction = async (action: AboutAction) => {
    setBusy(true);
    try {
      const answer = await send(action);
      queryClient.setQueryData(aboutKey, answer);
      await queryClient.invalidateQueries({ queryKey: profileKey });
    } catch (error) {
      const theirs = staleAbout(error);
      if (theirs) queryClient.setQueryData(aboutKey, theirs);
      toast.error(describeError(error));
    } finally {
      setBusy(false);
    }
  };

  /** Done on a part: send what waits, then the model reads the part. True when the editor may close. */
  const finishPart = async (heading: string): Promise<boolean> => {
    const saved = await flush();
    if (!saved) return false;
    // A fact part is saved by code, with or without a key; the others need one.
    const kind = shownPart(saved, heading)?.kind;
    if (saved.needs === "model_key" && kind !== "facts") return true;
    try {
      queryClient.setQueryData(
        aboutKey,
        await doneAbout(heading, saved.version),
      );
      return true;
    } catch (error) {
      const theirs = staleAbout(error);
      if (theirs) queryClient.setQueryData(aboutKey, theirs);
      toast.error(describeError(error));
      return false;
    }
  };

  return { busy, runAction, finishPart };
}
