import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  getVersion,
  listVersions,
  versionKey,
  versionsKey,
  type DocumentOut,
} from "./documents-api";
import { withLatest } from "../helpers/versions";

/** The versions of a document; the See view's history and the other views read this one list. */
export function useVersions(document: DocumentOut) {
  return useQuery({
    // A new version comes with a render or a generation: the key follows the document's latest.
    queryKey: [...versionsKey(document.id), document.latest?.number ?? 0],
    queryFn: ({ signal }) => listVersions(document.id, signal),
  });
}

/** One version of a document, with its content and what the fact check corrected. */
export function useVersion(documentId: string, number: number) {
  return useQuery({
    queryKey: versionKey(documentId, number),
    queryFn: ({ signal }) => getVersion(documentId, number, signal),
  });
}

/** The versions of a document and the one the See view shows: the chosen one, else the latest. */
export function useVersionHistory(document: DocumentOut) {
  const [chosen, setChosen] = useState<number>();
  const latestNumber = document.latest?.number;
  const history = useVersions(document);
  const rows = withLatest(document.latest, history.data ?? []);
  const shown =
    rows.find((one) => one.number === chosen) ?? document.latest ?? undefined;
  return { history, rows, shown, latestNumber, choose: setChosen };
}
