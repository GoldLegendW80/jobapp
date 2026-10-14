import { PublicLayout } from "./public-layout";
import {
  TERMS_PRIVATE,
  TERMS_RESPONSIBILITY,
  TERMS_WARRANTY,
  VERSION,
} from "./texts";

/** The terms of use: a short plain statement. Public: no session. */
export function TermsPage() {
  return (
    <PublicLayout title="Terms">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Terms</h1>
        <p className="text-sm text-muted-foreground">Version {VERSION}</p>
      </div>
      <ul className="list-disc space-y-2 pl-6">
        <li>{TERMS_PRIVATE}</li>
        <li>{TERMS_WARRANTY}</li>
        <li>{TERMS_RESPONSIBILITY}</li>
      </ul>
    </PublicLayout>
  );
}
