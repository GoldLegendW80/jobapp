import { Search } from "lucide-react";
import { useId } from "react";

import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";

import { useDebouncedField } from "@/features/jobs/hooks/use-job-filters";
import {
  SEARCH_DEBOUNCE_MS,
  TEXT_MAX,
  type FilterProps,
} from "@/features/jobs/helpers/filters";

/** The search box: company, title, location and notes, committed after a pause or on Enter. */
export function SearchField({
  filters,
  onChange,
  className,
}: FilterProps & { className?: string }) {
  const search = useDebouncedField(
    filters.q,
    (q) => {
      onChange({ ...filters, q });
    },
    SEARCH_DEBOUNCE_MS,
  );
  const id = useId();
  return (
    <InputGroup className={className}>
      <Label htmlFor={id} className="sr-only">
        Search postings
      </Label>
      <InputGroupInput
        id={id}
        type="search"
        placeholder="Search company, title, location, notes"
        maxLength={TEXT_MAX}
        value={search.draft}
        onChange={(event) => {
          search.setDraft(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") search.flush();
        }}
      />
      <InputGroupAddon>
        <Search aria-hidden="true" />
      </InputGroupAddon>
    </InputGroup>
  );
}
