import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  conflictOf,
  profileKey,
  saveSection,
  type FactBlock,
  type Profile,
} from "./profile-api";
import { orderBlocks } from "../helpers/fact-base";

/** Saves the facts section with `blocks` in the order of `titles`; null when nothing moves. */
function sendOrder(
  revision: number,
  blocks: readonly FactBlock[],
  titles: readonly string[],
): Promise<Profile> | null {
  const ordered = orderBlocks(blocks, titles);
  return ordered === null
    ? null
    : saveSection("facts", revision, { blocks: ordered });
}

/**
 * The order of the fact blocks, saved as a revision of the facts section when a fact part of
 * About you moves (owner, 2026-11-07), so History shows it and a restore undoes it. An order
 * never overwrites a text: on a 409 the same order is put on the blocks saved meanwhile, once.
 */
export function useSaveFactOrder(on: { onError: (error: Error) => void }) {
  const client = useQueryClient();
  return useMutation({
    // One at a time, each on the revision the one before saved.
    scope: { id: "fact-order" },
    mutationFn: async (titles: readonly string[]) => {
      const profile = client.getQueryData<Profile>(profileKey);
      if (!profile) return null;
      try {
        return await sendOrder(
          profile.revision,
          profile.sections.facts.blocks,
          titles,
        );
      } catch (error) {
        const current = conflictOf(error);
        if (!current) throw error;
        return sendOrder(
          current.revision,
          current.sections.facts.blocks,
          titles,
        );
      }
    },
    onSuccess: (saved) => {
      if (saved) client.setQueryData(profileKey, saved);
    },
    onError: on.onError,
  });
}
