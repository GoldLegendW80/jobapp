import { isRecord, words } from "@/features/ingestion/helpers/proposal-value";
import { scalar } from "@/features/ingestion/helpers/proposal-words";

/** A value of the profile as text: one line for a single value, one line per field otherwise. */
export function ProfileValue({
  value,
  label,
}: {
  value: unknown;
  label: string;
}) {
  if (value === null || value === undefined || value === "")
    return <p className="text-sm text-muted-foreground">Nothing yet</p>;
  if (!isRecord(value))
    return <p className="text-sm break-words">{scalar(value)}</p>;
  const fields = Object.entries(value).filter(
    ([name, held]) => held !== null && held !== "" && name !== "key",
  );
  return (
    <dl
      aria-label={label}
      className="grid grid-cols-[auto_1fr] gap-x-3 text-sm"
    >
      {fields.map(([name, held]) => (
        <div key={name} className="contents">
          <dt className="text-muted-foreground">{words(name)}</dt>
          <dd className="min-w-0 break-words">{scalar(held)}</dd>
        </div>
      ))}
    </dl>
  );
}
