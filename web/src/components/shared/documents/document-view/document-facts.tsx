import { Section } from "@/components/blocks/section";
import { Badge } from "@/components/ui/badge";
import { Fact, Facts } from "@/components/blocks/facts";
import { Card, CardContent } from "@/components/ui/card";

import type {
  DocumentOut,
  VersionSummary,
} from "@/features/documents/api/documents-api";
import { fitLabel, templateLabel } from "@/features/documents/helpers/labels";

/** The document's template and language, and whether the version shown fits one page. */
export function DocumentFacts({
  document,
  shown,
}: {
  document: DocumentOut;
  shown: VersionSummary | undefined;
}) {
  return (
    <Section title="Details">
      <Card>
        <CardContent>
          <Facts>
            <Fact label="Template">
              {templateLabel(document.template_slug)}
            </Fact>
            <Fact label="Language">
              {document.language === "fr" ? "French" : "English"}
            </Fact>
            {shown !== undefined ? (
              <Fact label="Length">
                <Badge
                  data-testid="fit"
                  variant={
                    shown.fit === "overflow"
                      ? "destructive"
                      : shown.fit === "one_page"
                        ? "success"
                        : "outline"
                  }
                >
                  {fitLabel(shown)}
                </Badge>
              </Fact>
            ) : null}
          </Facts>
        </CardContent>
      </Card>
    </Section>
  );
}
