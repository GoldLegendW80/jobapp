import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { parseFilters, toSearchParams, type JobFilters } from "./filters";

/**
 * The filters of /jobs, read from and written to the URL query. Every change is a new history
 * entry, so back and forward step through the views. The parsed value is stable while the
 * canonical query does not change, so it can sit in effect dependencies.
 */
export function useJobFilters() {
  const [params, setParams] = useSearchParams();
  const canonical = toSearchParams(parseFilters(params)).toString();
  const filters = useMemo(
    () => parseFilters(new URLSearchParams(canonical)),
    [canonical],
  );
  const setFilters = useCallback(
    (next: JobFilters) => {
      const query = toSearchParams(next);
      if (query.toString() === canonical) return;
      setParams(query);
    },
    [canonical, setParams],
  );
  return { filters, setFilters, query: canonical };
}

/**
 * A text field bound to a URL value: typing updates the field at once and commits after
 * `delayMs` of quiet; a URL change from elsewhere (back, Clear filters) replaces the field.
 */
export function useDebouncedField(
  value: string,
  commit: (value: string) => void,
  delayMs = 300,
) {
  const [draft, setDraft] = useState(value);
  const committed = useRef(value);
  const commitRef = useRef(commit);
  useEffect(() => {
    commitRef.current = commit;
  });

  useEffect(() => {
    if (value !== committed.current) {
      committed.current = value;
      setDraft(value);
    }
  }, [value]);

  useEffect(() => {
    if (draft.trim() === committed.current) return;
    const timer = setTimeout(() => {
      committed.current = draft.trim();
      commitRef.current(draft.trim());
    }, delayMs);
    return () => {
      clearTimeout(timer);
    };
  }, [draft, delayMs]);

  const flush = useCallback(() => {
    if (draft.trim() === committed.current) return;
    committed.current = draft.trim();
    commitRef.current(draft.trim());
  }, [draft]);

  return { draft, setDraft, flush };
}
