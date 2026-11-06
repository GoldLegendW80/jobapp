import { cn } from "@/lib/utils";

import type { RowKind } from "@/features/jobs/helpers/sources";
import { BLOCK, Glyph, HANG } from "../setup-grid";
import { SourceKindIcon } from "./source-kind-icon";

/** The kinds of address the list takes, each with an example. */
export function AddressKinds() {
  return (
    <div className={BLOCK}>
      <p className="text-sm text-muted-foreground">
        Add any website with job offers:
      </p>
      <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
        <AddressKind
          kind="website"
          text="A job website, like LinkedIn or Indeed"
        />
        <AddressKind
          kind="board"
          text="A company job board, like jobs.ashbyhq.com/doctolib"
        />
        <AddressKind
          kind="website"
          text="Any careers page, like https://www.example.com/careers"
        />
      </ul>
    </div>
  );
}

function AddressKind({ kind, text }: { kind: RowKind; text: string }) {
  return (
    <li className={cn(HANG, "items-start")}>
      <Glyph>
        <SourceKindIcon kind={kind} className="size-6 rounded-md" />
      </Glyph>
      <span className="flex min-h-7 min-w-0 items-center break-words">
        {text}
      </span>
    </li>
  );
}
