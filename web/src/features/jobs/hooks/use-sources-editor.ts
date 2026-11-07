/**
 * The changes the list of addresses makes to its draft (sources.ts), with what went wrong and
 * the sentence a screen reader hears after each. The draft and its save live in `useSources`.
 */
import { useState } from "react";

import type { CompanyBoard } from "@/features/profile/api/profile-api";
import { describeError, isApiError } from "@/lib/api";

import { savedBoard } from "../api/boards-api";
import { useDetectBoard } from "../api/use-board-ideas";
import { FEED_NAMES } from "../helpers/job-feeds";
import { platformOf, type Protected } from "../helpers/site-permissions";
import {
  SITES_MAX,
  checkSite,
  addressProblem,
  feedOf,
  namesSentence,
  resumed,
  shortName,
  siteOf,
  withBoard,
  withFeed,
  withRecommended,
  withScheme,
  withoutRow,
  type SourceRow,
} from "../helpers/sources";
import type { Sources } from "../api/use-sources";

export function useSourcesEditor(sources: Sources) {
  const { draft, setDraft, pending, setPending } = sources;
  const [adding, setAdding] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const detectBoard = useDetectBoard();

  const done = (sentence: string) => {
    setProblem(null);
    setSaid(sentence);
  };

  const addSite = (url: string, name?: string, preset?: Protected) => {
    const checked = checkSite(draft.sites, siteOf(url, preset));
    if ("error" in checked) {
      setProblem(checked.error);
      return false;
    }
    setDraft((current) => ({ ...current, sites: checked.sites }));
    done(`${name ?? shortName(url)} added.`);
    return true;
  };

  /** Adds what the person typed or picked; true when it is in the list now. */
  const add = async (
    raw: string,
    name?: string,
    preset?: Protected,
  ): Promise<boolean> => {
    const wrong = addressProblem(raw);
    if (wrong) {
      setProblem(wrong);
      return false;
    }
    const url = withScheme(raw);
    const feed = feedOf(url);
    if (feed) {
      if (draft.feeds.includes(feed)) {
        setProblem("This website is already in your list.");
        return false;
      }
      setDraft((current) => ({
        ...current,
        feeds: withFeed(current.feeds, feed),
      }));
      done(`${FEED_NAMES[feed]} added.`);
      return true;
    }
    if (preset || platformOf(siteOf(url))) return addSite(url, name, preset);
    // A company job board is read through its official API: the api names its ATS and id.
    setAdding(true);
    try {
      const found = await detectBoard(url);
      const next = withBoard(draft.boards ?? [], savedBoard(found));
      if ("error" in next) {
        setProblem(next.error);
        return false;
      }
      setDraft((current) => ({ ...current, boards: next.boards }));
      done(`${found.company} added.`);
      return true;
    } catch (failure) {
      if (isApiError(failure) && failure.status === 422)
        return addSite(url, name, preset);
      setProblem(`The address could not be checked. ${describeError(failure)}`);
      return false;
    } finally {
      setAdding(false);
    }
  };

  const addTyped = async () => {
    if (await add(pending)) setPending("");
  };

  const editAddress = (text: string) => {
    setPending(text);
    setProblem(null);
  };

  const addBoards = (boards: readonly CompanyBoard[]) => {
    let list = draft.boards ?? [];
    let added = 0;
    for (const board of boards) {
      const next = withBoard(list, board);
      if ("error" in next) {
        setProblem(next.error);
        break;
      }
      list = next.boards;
      added += 1;
    }
    setDraft((current) => ({ ...current, boards: list }));
    const [only] = boards;
    setSaid(
      added === 1 && only
        ? `${only.company} added.`
        : `${String(added)} company boards added.`,
    );
  };

  const addRecommended = () => {
    const result = withRecommended(draft);
    setDraft(result.draft);
    if (result.left.length) {
      setProblem(
        `Keep at most ${String(SITES_MAX)} websites: ${namesSentence(result.left)} ${result.left.length === 1 ? "was" : "were"} not added. Remove one to add another.`,
      );
      setSaid(
        result.added.length ? `${namesSentence(result.added)} added.` : "",
      );
      return;
    }
    done(`${namesSentence(result.added)} added.`);
  };

  const remove = (row: SourceRow) => {
    setDraft((current) => withoutRow(current, row));
    done(`${row.name} removed.`);
  };

  const resume = (row: SourceRow) => {
    setDraft((current) => resumed(current, row));
    done(`${row.name} is searched again.`);
  };

  return {
    adding,
    problem,
    said,
    add,
    addTyped,
    editAddress,
    addBoards,
    addRecommended,
    remove,
    resume,
  };
}
