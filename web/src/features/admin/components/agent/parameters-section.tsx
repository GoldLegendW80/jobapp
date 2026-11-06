import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { AgentPage } from "../../api/agents-api";
import { AgentSection } from "./agent-section";
import { FoldedJson } from "./folded-json";

const HEADS = ["Field", "Type", "Required", "Default", "Limits", "Description"];

/** The fields of a run's input, and their JSON schema. */
export function ParametersSection({ page }: { page: AgentPage }) {
  return (
    <AgentSection id="input" title="Parameters it takes">
      {page.input_fields.length ? (
        <Table aria-label="Input fields">
          <TableHeader>
            <TableRow>
              {HEADS.map((head) => (
                <TableHead key={head}>{head}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.input_fields.map((field) => (
              <TableRow key={field.name} className="align-top">
                <TableHead>
                  <code>{field.name}</code>
                </TableHead>
                <TableCell label="Type">{field.type}</TableCell>
                <TableCell label="Required">
                  {field.required ? "Yes" : "No"}
                </TableCell>
                <TableCell label="Default">
                  {field.default === null || field.default === undefined
                    ? "None"
                    : JSON.stringify(field.default)}
                </TableCell>
                <TableCell label="Limits">
                  <span className="[overflow-wrap:anywhere]">
                    {field.limits || "None"}
                  </span>
                </TableCell>
                <TableCell label="Description">
                  {field.description || "None"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm text-muted-foreground">It takes no parameter.</p>
      )}
      <FoldedJson label="Its JSON schema" value={page.input_schema} />
    </AgentSection>
  );
}
