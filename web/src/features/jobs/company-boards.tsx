import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CompanyBoard } from "@/features/profile/api";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import {
  ATS_NAMES,
  BOARDS_MAX,
  boardKey,
  boardPage,
  detectBoard,
  getBoardSuggestions,
  savedBoard,
  suggestionsKey,
} from "./boards-api";

/**
 * "Company job boards" in Find jobs (owner, 2026-10-30): the companies whose public job board
 * every search reads through its ATS's API (Ashby, Greenhouse, Lever, SmartRecruiters, Workable,
 * Recruitee). Paste a board address to add it: the api names its ATS and id. Boards the
 * person's own postings point to are suggested, never added by themselves. `boards` is null
 * while the starter list loads; the parent saves the list with the search.
 */
export function CompanyBoards({
  boards,
  onChange,
}: {
  boards: CompanyBoard[] | null;
  onChange: (next: CompanyBoard[]) => void;
}) {
  const id = useId();
  const [address, setAddress] = useState("");
  const [adding, setAdding] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const suggestions = useQuery({
    queryKey: suggestionsKey,
    queryFn: ({ signal }) => getBoardSuggestions(signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const list = boards ?? [];
  const listed = new Set(list.map(boardKey));
  const add = (board: CompanyBoard): boolean => {
    if (listed.has(boardKey(board))) {
      setProblem(`${board.company} is already in your list.`);
      return false;
    }
    if (list.length >= BOARDS_MAX) {
      setProblem(
        `Keep at most ${String(BOARDS_MAX)} boards. Remove one to add another.`,
      );
      return false;
    }
    onChange([...list, { ...board, enabled: true }]);
    setProblem(null);
    return true;
  };
  const addAddress = async () => {
    const text = address.trim();
    if (!text) return;
    setAdding(true);
    setProblem(null);
    try {
      const found = await detectBoard(text);
      if (add(savedBoard(found))) setAddress("");
    } catch (failure) {
      setProblem(
        isApiError(failure) && failure.status === 422
          ? (errorDetail(failure) ?? "This address is not a company job board.")
          : `The address was not checked. ${describeError(failure)}`,
      );
    } finally {
      setAdding(false);
    }
  };
  const suggested = (suggestions.data?.boards ?? []).filter(
    (board) => !listed.has(boardKey(board)),
  );
  return (
    <section className="space-y-3" aria-labelledby={`${id}-boards`}>
      <h3 id={`${id}-boards`} className="font-medium">
        Company job boards
      </h3>
      <p className="text-sm text-muted-foreground">
        Every search also reads these companies&apos; public job boards through
        their official API, then keeps the postings that match your criteria.
        Ashby, Greenhouse, Lever, SmartRecruiters, Workable and Recruitee are
        supported. Untick a board to pause it.
      </p>
      {boards === null ? (
        <p className="text-sm text-muted-foreground">Loading the boards…</p>
      ) : list.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No company board yet. Paste one below.
        </p>
      ) : (
        <ul
          aria-label="Company job boards"
          className="max-h-64 divide-y overflow-y-auto rounded-md border"
        >
          {list.map((board, index) => (
            <li
              key={boardKey(board)}
              className="flex min-w-0 flex-wrap items-center gap-2 p-2 text-sm"
            >
              <label className="flex min-w-0 flex-1 items-center gap-2">
                <input
                  type="checkbox"
                  className="size-4 shrink-0 accent-primary"
                  checked={board.enabled}
                  onChange={(event) => {
                    const enabled = event.target.checked;
                    onChange(
                      list.map((item, at) =>
                        at === index ? { ...item, enabled } : item,
                      ),
                    );
                  }}
                />
                <span className="truncate font-medium">{board.company}</span>
              </label>
              <a
                href={boardPage(board)}
                target="_blank"
                rel="noreferrer noopener"
                className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              >
                {ATS_NAMES[board.ats]}
              </a>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Remove ${board.company}`}
                onClick={() => {
                  onChange(list.filter((_, at) => at !== index));
                }}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="space-y-2">
        <Label htmlFor={`${id}-board`}>Board address</Label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            id={`${id}-board`}
            type="url"
            maxLength={2000}
            value={address}
            onChange={(event) => {
              setAddress(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void addAddress();
              }
            }}
            placeholder="https://jobs.ashbyhq.com/example"
          />
          <Button
            type="button"
            variant="outline"
            disabled={adding || boards === null || !address.trim()}
            onClick={() => void addAddress()}
          >
            {adding ? "Checking…" : "Add board"}
          </Button>
        </div>
        {problem ? (
          <p role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        ) : null}
      </div>
      {suggested.length > 0 && boards !== null ? (
        <div className="space-y-2">
          <p className="text-sm font-medium">Suggested from your postings</p>
          <ul aria-label="Suggested boards" className="flex flex-wrap gap-2">
            {suggested.map((board) => (
              <li key={boardKey(board)}>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={`Add ${board.company} (${ATS_NAMES[board.ats]})`}
                  onClick={() => {
                    add(savedBoard(board));
                  }}
                >
                  {board.company}, {ATS_NAMES[board.ats]}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
