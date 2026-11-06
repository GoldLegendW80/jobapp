import { FilePlus2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

import {
  joinParts,
  splitParts,
  type About,
  type Part,
} from "../../api/about-api";
import { useAboutActions } from "@/features/profile/api/use-about-actions";
import { useAboutAutosave } from "@/features/profile/api/use-about-autosave";
import {
  kindAnchors,
  newPartHeading,
  shownPart,
} from "@/features/profile/helpers/about-parts";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";
import { AboutContents } from "./about-contents";
import { AboutPartCard } from "./about-part-card";
import { SaveConflictAlert } from "./save-conflict-alert";

/** The loaded document: the contents beside its parts, one part edited at a time. */
export function AboutEditor({ about }: { about: About }) {
  const doc = useAboutAutosave(about);
  const actions = useAboutActions(doc.flush);
  const [editing, setEditing] = useState<number | null>(null);
  const { preamble, parts } = splitParts(doc.text);
  const anchors = kindAnchors(about, parts);

  const setPart = (index: number, part: Part) => {
    const next = parts.map((one, i) => (i === index ? part : one));
    doc.change(joinParts(preamble, next));
  };

  const addPart = () => {
    const heading = newPartHeading(parts);
    doc.change(joinParts(preamble, [...parts, { heading, body: "" }]));
    setEditing(parts.length);
  };

  return (
    <div className="flex flex-col gap-4">
      {doc.status.state === "conflict" ? (
        <SaveConflictAlert onSettle={doc.settle} />
      ) : null}
      <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-6">
        <AboutContents parts={parts} />
        <div className="flex min-w-0 flex-col gap-4">
          {parts.map((part, index) => (
            <AboutPartCard
              kindAnchor={anchors[index]}
              key={index}
              index={index}
              about={about}
              part={part}
              shown={shownPart(about, part.heading)}
              editing={editing === index}
              busy={actions.busy}
              status={doc.status}
              onEdit={() => {
                setEditing(index);
              }}
              onChange={(next) => {
                setPart(index, next);
              }}
              onDone={async (heading) => {
                if (await actions.finishPart(heading)) setEditing(null);
              }}
              onAct={(action) => void actions.runAction(action)}
            />
          ))}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              onClick={addPart}
              disabled={editing !== null}
            >
              <FilePlus2 aria-hidden="true" />
              {W.addPart}
            </Button>
            {about.missing_blocks.length > 0 ? (
              <p className="text-sm text-muted-foreground">
                {W.missing(about.missing_blocks.length)}{" "}
                <Button
                  variant="link"
                  className="h-auto p-0"
                  disabled={actions.busy}
                  onClick={() => {
                    void actions.runAction({
                      name: "rewrite",
                      heading: null,
                      version: about.version,
                    });
                  }}
                >
                  {W.addThem}
                </Button>
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
