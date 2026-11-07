import { LoadError } from "@/components/blocks/load-error";
import { Section } from "@/components/blocks/section";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfile } from "@/features/profile/api/use-profile";

import type {
  DocumentOut,
  VersionSummary,
} from "@/features/documents/api/documents-api";
import { useVersion } from "@/features/documents/api/use-version-history";
import {
  pageFacts,
  profilePhoto,
} from "@/features/documents/helpers/page-facts";
import { DocumentSheet } from "../document-sheet";
import type { PageHeading } from "../edit-panel";

/** An earlier version opened from Versions: its page as it was written, read only. */
export function VersionSheet({
  document,
  version,
  heading,
}: {
  document: DocumentOut;
  version: VersionSummary;
  heading: PageHeading;
}) {
  const query = useVersion(document.id, version.number);
  const profile = useProfile();
  return (
    <>
      <Section title={heading.title} description={heading.meta}>
        {heading.fit}
        <p className="text-sm text-muted-foreground">
          An earlier version, as it was written. To change its text, restore it
          from Versions: it becomes the draft you edit here.
        </p>
        {query.isPending ? (
          <AspectRatio ratio={210 / 297}>
            <Skeleton className="size-full rounded-md" />
          </AspectRatio>
        ) : query.isError ? (
          <LoadError query={query}>This version could not be loaded.</LoadError>
        ) : (
          <DocumentSheet
            kind={document.kind}
            language={version.language}
            template={version.template_slug}
            step={version.fit_step}
            photo={profilePhoto(profile.data)}
            content={query.data.content}
            facts={pageFacts(
              profile.data?.sections,
              document.scope === "base" ? query.data.content : {},
            )}
            form={null}
            known={false}
          />
        )}
      </Section>
      {heading.below}
    </>
  );
}
