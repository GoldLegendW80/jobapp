import { PageHeader } from "@/components/blocks/page-header";
import { Card, CardContent } from "@/components/ui/card";

import { PublicLayout } from "@/components/shared/public/public-layout";
import {
  TERMS_PRIVATE,
  TERMS_RESPONSIBILITY,
  TERMS_WARRANTY,
  VERSION,
} from "../helpers/texts";

/** The terms of use: a short plain statement. Public: no session. */
export function TermsPage() {
  return (
    <PublicLayout title="Terms">
      <PageHeader title="Terms" description={`Version ${VERSION}`} />
      <Card>
        <CardContent>
          <ul className="list-disc space-y-2 pl-6">
            <li>{TERMS_PRIVATE}</li>
            <li>{TERMS_WARRANTY}</li>
            <li>{TERMS_RESPONSIBILITY}</li>
          </ul>
        </CardContent>
      </Card>
    </PublicLayout>
  );
}
