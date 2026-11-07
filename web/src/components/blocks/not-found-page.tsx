import { SearchX } from "lucide-react";
import { Link } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { Page } from "@/components/blocks/page";
import { Button } from "@/components/ui/button";
import { useTitle } from "@/hooks/use-title";

/** Any address the app does not know, and a page the person may not see: the same answer. */
export function NotFoundPage() {
  useTitle("Page not found");
  return (
    <Page>
      <EmptyState
        icon={SearchX}
        title={<h1 className="text-xl tracking-tight">Page not found</h1>}
        description="This page does not exist, or you do not have access to it."
        actions={
          <Button asChild>
            <Link to="/">Go to the home page</Link>
          </Button>
        }
      />
    </Page>
  );
}
