import type { ReactNode } from "react";

import type {
  OfferNotes,
  OfferNotesContent,
} from "@/features/documents/api/offer-notes-api";
import {
  comparedWith,
  NOT_ADVICE,
} from "@/features/documents/helpers/offer-notes";

/**
 * The notes as the person reads them, in the letter's language: the summary, each clause in
 * plain words under its own words, what differs, the questions for a lawyer, then what the
 * letter was compared with. Plain text, never markdown.
 */
export function OfferNotesView({
  notes,
  language,
  pay,
}: {
  notes: OfferNotesContent;
  language: string | null;
  pay: OfferNotes["pay"];
}) {
  return (
    <div className="flex flex-col gap-6" data-testid="offer-notes">
      <div lang={language ?? undefined} className="flex flex-col gap-6">
        {notes.summary ? (
          <p className="max-w-prose whitespace-pre-line">{notes.summary}</p>
        ) : null}
        <Part title="Clauses" when={notes.clauses.length > 0}>
          <ol className="flex flex-col gap-4">
            {notes.clauses.map((clause, index) => (
              <li key={index} className="flex flex-col gap-1">
                <h4 className="font-medium">{clause.title}</h4>
                {clause.quote ? (
                  <blockquote className="max-w-prose border-l-2 pl-3 text-sm whitespace-pre-line text-muted-foreground italic">
                    {clause.quote}
                  </blockquote>
                ) : null}
                <p className="max-w-prose whitespace-pre-line">
                  {clause.plain}
                </p>
              </li>
            ))}
          </ol>
        </Part>
        <Part title="What differs" when>
          {notes.differences.length > 0 ? (
            <Lines lines={notes.differences} />
          ) : (
            <p className="text-sm text-muted-foreground" lang="en">
              Nothing differs from the posting or from the pay you seek.
            </p>
          )}
        </Part>
        <Part title="Questions for a lawyer" when={notes.questions.length > 0}>
          <Lines lines={notes.questions} />
        </Part>
      </div>
      <div className="flex flex-col gap-1 text-sm text-muted-foreground">
        <p>{comparedWith(pay)}</p>
        <p>{NOT_ADVICE}</p>
      </div>
    </div>
  );
}

function Part({
  title,
  when,
  children,
}: {
  title: string;
  when: boolean;
  children: ReactNode;
}) {
  if (!when) return null;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-muted-foreground" lang="en">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Lines({ lines }: { lines: readonly string[] }) {
  return (
    <ul className="flex max-w-prose list-disc flex-col gap-1 pl-5">
      {lines.map((line, index) => (
        <li key={index} className="whitespace-pre-line">
          {line}
        </li>
      ))}
    </ul>
  );
}
