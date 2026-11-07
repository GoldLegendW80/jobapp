import { MessageSquare } from "lucide-react";

import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";

import { ChatPanel } from "../chat-panel";
import type { PanelProps } from "../document-view";

const TITLE = "Ask for a change";
const DESCRIPTION = "Each change is a new version you can restore.";

/** The chat in the document view's side rail. */
export function ChatSection(props: PanelProps) {
  return (
    <Section title={TITLE} description={DESCRIPTION}>
      <Card>
        <CardContent>
          <ChatPanel {...props} />
        </CardContent>
      </Card>
    </Section>
  );
}

/** On a phone, the chat opens from the page header in a Drawer from the bottom of the screen. */
export function ChatSheet(props: PanelProps) {
  return (
    <Drawer>
      <DrawerTrigger asChild>
        <Button variant="outline">
          <MessageSquare aria-hidden="true" />
          {TITLE}
        </Button>
      </DrawerTrigger>
      <DrawerContent className="gap-0">
        <DrawerHeader>
          <DrawerTitle>{TITLE}</DrawerTitle>
          <DrawerDescription>{DESCRIPTION}</DrawerDescription>
        </DrawerHeader>
        {/* Dragged by its top only: below, a drag scrolls the conversation or selects text. */}
        <div
          data-vaul-no-drag
          className="min-h-0 overflow-y-auto px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))]"
        >
          <ChatPanel {...props} />
        </div>
      </DrawerContent>
    </Drawer>
  );
}
