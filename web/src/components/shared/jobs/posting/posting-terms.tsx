import { Badge } from "@/components/ui/badge";

/** The competencies the person has, or the gaps, as a titled row of badges. */
export function PostingTerms({
  label,
  terms,
  tone,
}: {
  label: string;
  terms: string[];
  tone: "have" | "gap";
}) {
  if (terms.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{label}</h3>
      <ul aria-label={label} className="flex flex-wrap gap-1.5">
        {terms.map((term) => (
          <li key={term}>
            <Badge
              variant="outline"
              className={
                tone === "have"
                  ? "border-success/30 bg-success/10 text-foreground"
                  : "border-warning/40 bg-warning-muted text-foreground"
              }
            >
              {term}
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}
