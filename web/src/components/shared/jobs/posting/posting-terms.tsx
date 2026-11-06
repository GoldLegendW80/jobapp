import { Badge } from "@/components/ui/badge";

/** The competencies the person has, or the gaps, as a titled row of badges. */
export function PostingTerms({
  label,
  terms,
  tone,
  heading,
}: {
  label: string;
  terms: string[];
  tone: "have" | "gap";
  heading: "h2" | "h3";
}) {
  if (terms.length === 0) return null;
  const Heading = heading;
  return (
    <div className="flex flex-col gap-1.5">
      <Heading className="text-xs font-medium text-muted-foreground">
        {label}
      </Heading>
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
