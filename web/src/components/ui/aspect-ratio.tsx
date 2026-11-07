import { AspectRatio as AspectRatioPrimitive } from "radix-ui";

/** Keeps a box at a ratio while its width changes: a page thumbnail (210 by 297), a screen (16 by 10). */
function AspectRatio({
  ...props
}: React.ComponentProps<typeof AspectRatioPrimitive.Root>) {
  return <AspectRatioPrimitive.Root data-slot="aspect-ratio" {...props} />;
}

export { AspectRatio };
