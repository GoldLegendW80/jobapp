import { FileWarning } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

import type { VersionSummary } from "@/features/documents/api/documents-api";

/**
 * Beside the fit note: what the check of the version's PDF text layer found (SPEC-documents.md,
 * "Being careful", the finished PDF), each finding in the api's sentence with what to do. A
 * finding never holds the PDF back; nothing shows when the check passed or never ran.
 */
export function PdfTextNote({ version }: { version: VersionSummary }) {
  const findings = version.pdf_text ?? [];
  if (findings.length === 0) return null;
  return (
    <Alert variant="warning" data-testid="pdf-text-note">
      <FileWarning aria-hidden="true" />
      <AlertTitle>
        An application system may not read all of this PDF
      </AlertTitle>
      <AlertDescription className="flex flex-col items-start gap-2">
        <ul className="flex list-disc flex-col gap-1 pl-4">
          {findings.map((finding) => (
            <li key={finding.code}>{finding.message}</li>
          ))}
        </ul>
        <p>It can still be downloaded and sent as it is.</p>
      </AlertDescription>
    </Alert>
  );
}
