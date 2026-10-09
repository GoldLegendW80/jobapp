import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CircleAlert,
  ImageUp,
  LoaderCircle,
  Trash2,
  UserRound,
} from "lucide-react";
import { useId, useRef, useState, type PointerEvent } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import {
  deletePhoto,
  photoUrl,
  profileKey,
  uploadPhoto,
  type Profile,
} from "./api";
import {
  CENTERED,
  checkFile,
  cropRect,
  cropToBlob,
  loadImage,
  MIN_SIZE,
  type Crop,
  type LoadedImage,
} from "./crop";

/** The api's sentence for a refused upload (413, 415, 422), or a generic one. */
function uploadError(error: unknown): string {
  const detail = errorDetail(error);
  if (
    isApiError(error) &&
    [413, 415, 422].includes(error.status ?? 0) &&
    detail
  )
    return detail;
  return describeError(error);
}

/**
 * The photo block of the Identity tab: the stored photo (or a placeholder), "Upload photo" which
 * opens the square crop, and "Remove photo". Saved at once, independent of the form's Save.
 */
export function PhotoField({ profile }: { profile: Profile }) {
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [chosen, setChosen] = useState<{
    loaded: LoadedImage;
    mime: "image/png" | "image/jpeg";
  }>();
  const [pickError, setPickError] = useState<string>();

  const setPhoto = (etag: string | null) => {
    client.setQueryData<Profile>(profileKey, (old) =>
      old ? { ...old, has_photo: etag !== null, photo_etag: etag } : old,
    );
  };

  const remove = useMutation({
    mutationFn: deletePhoto,
    onSuccess: () => {
      setPhoto(null);
      toast.success("Photo removed.");
    },
    onError: (error) => {
      toast.error(`The photo was not removed. ${describeError(error)}`);
    },
  });

  const onFile = async (file: File | undefined) => {
    setPickError(undefined);
    if (!file) return;
    const checked = await checkFile(file);
    if (!checked.ok) {
      setPickError(checked.error);
      return;
    }
    try {
      const loaded = await loadImage(file);
      setChosen({ loaded, mime: checked.mime });
    } catch (error) {
      setPickError(error instanceof Error ? error.message : String(error));
    }
  };

  const etag = profile.has_photo ? profile.photo_etag : null;
  return (
    <section aria-labelledby="photo-heading" className="flex flex-col gap-3">
      <h3 id="photo-heading" className="text-sm font-medium">
        Photo
      </h3>
      <div className="flex flex-wrap items-center gap-4">
        <div className="grid size-24 shrink-0 place-items-center overflow-hidden rounded-full border bg-muted">
          {etag ? (
            <img
              src={photoUrl(etag)}
              alt="Your profile photo"
              className="size-full object-cover"
            />
          ) : (
            <UserRound
              aria-hidden="true"
              className="size-10 text-muted-foreground"
            />
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => input.current?.click()}
          >
            <ImageUp aria-hidden="true" />
            {etag ? "Change photo" : "Upload photo"}
          </Button>
          {etag ? (
            <Button
              type="button"
              variant="ghost"
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate();
              }}
            >
              <Trash2 aria-hidden="true" />
              {remove.isPending ? "Removing" : "Remove photo"}
            </Button>
          ) : null}
        </div>
        <input
          ref={input}
          type="file"
          accept="image/png,image/jpeg"
          aria-label="Photo file"
          className="sr-only"
          tabIndex={-1}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            void onFile(file);
          }}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        PNG or JPEG. You choose a square before it is uploaded.
      </p>
      {pickError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{pickError}</AlertDescription>
        </Alert>
      ) : null}
      {chosen ? (
        <CropDialog
          loaded={chosen.loaded}
          mime={chosen.mime}
          onClose={() => {
            chosen.loaded.release();
            setChosen(undefined);
          }}
          onUploaded={(newEtag) => {
            setPhoto(newEtag);
            toast.success("Photo saved.");
          }}
        />
      ) : null}
    </section>
  );
}

const PREVIEW = 128;

function CropDialog({
  loaded,
  mime,
  onClose,
  onUploaded,
}: {
  loaded: LoadedImage;
  mime: "image/png" | "image/jpeg";
  onClose: () => void;
  onUploaded: (etag: string) => void;
}) {
  const [crop, setCrop] = useState<Crop>(CENTERED);
  const [error, setError] = useState<string>();
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const ids = useId();
  const { width, height } = loaded;
  const rect = cropRect(width, height, crop);

  const upload = useMutation({
    mutationFn: async () => {
      const blob = await cropToBlob(loaded.image, rect, mime);
      return uploadPhoto(blob);
    },
    onMutate: () => {
      setError(undefined);
    },
    onSuccess: (photo) => {
      onUploaded(photo.etag);
      onClose();
    },
    onError: (err) => {
      setError(isApiError(err) ? uploadError(err) : err.message);
    },
  });

  // A square image has nothing to move along that axis.
  const freeX = width - rect.side;
  const freeY = height - rect.side;

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current || !frame.current) return;
    const box = frame.current.getBoundingClientRect();
    const scale = width / Math.max(1, box.width);
    const dx = (event.clientX - drag.current.x) * scale;
    const dy = (event.clientY - drag.current.y) * scale;
    drag.current = { x: event.clientX, y: event.clientY };
    setCrop((c) => ({
      ...c,
      x: freeX > 0 ? Math.min(1, Math.max(0, c.x + dx / freeX)) : c.x,
      y: freeY > 0 ? Math.min(1, Math.max(0, c.y + dy / freeY)) : c.y,
    }));
  };

  const pct = (value: number, total: number) => `${(100 * value) / total}%`;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !upload.isPending) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Crop your photo</DialogTitle>
          <DialogDescription>
            Drag the square or use the sliders to choose what shows. Only the
            square is uploaded.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
          <div
            ref={frame}
            className="relative w-full max-w-64 touch-none select-none"
            onPointerDown={(event) => {
              drag.current = { x: event.clientX, y: event.clientY };
              try {
                event.currentTarget.setPointerCapture(event.pointerId);
              } catch {
                // Not every engine supports pointer capture; dragging still works without it.
              }
            }}
            onPointerMove={onPointerMove}
            onPointerUp={() => {
              drag.current = null;
            }}
            onPointerCancel={() => {
              drag.current = null;
            }}
          >
            <img
              src={loaded.url}
              alt="The chosen image"
              draggable={false}
              className="block h-auto w-full rounded-md"
            />
            <div
              data-testid="crop-square"
              aria-hidden="true"
              className="absolute cursor-move rounded-sm border-2 border-white shadow-[0_0_0_9999px_rgb(0_0_0/0.45)] outline outline-1 outline-black/50"
              style={{
                left: pct(rect.sx, width),
                top: pct(rect.sy, height),
                width: pct(rect.side, width),
                height: pct(rect.side, height),
              }}
            />
          </div>
          <div className="flex flex-col items-center gap-2">
            <div
              role="img"
              aria-label="Preview of the square photo"
              className="rounded-full border"
              style={{
                width: PREVIEW,
                height: PREVIEW,
                backgroundImage: `url("${loaded.url}")`,
                backgroundRepeat: "no-repeat",
                backgroundSize: `${(width / rect.side) * PREVIEW}px ${(height / rect.side) * PREVIEW}px`,
                backgroundPosition: `${(-rect.sx / rect.side) * PREVIEW}px ${(-rect.sy / rect.side) * PREVIEW}px`,
              }}
            />
            <span className="text-xs text-muted-foreground">
              {Math.min(rect.side, 1024)} by {Math.min(rect.side, 1024)} pixels
            </span>
          </div>
        </div>
        <div className="grid gap-3">
          <Slider
            id={`${ids}-size`}
            label="Size"
            value={crop.size}
            min={MIN_SIZE}
            onChange={(size) => {
              setCrop((c) => ({ ...c, size }));
            }}
          />
          <Slider
            id={`${ids}-x`}
            label="Horizontal position"
            value={crop.x}
            disabled={freeX <= 0}
            onChange={(x) => {
              setCrop((c) => ({ ...c, x }));
            }}
          />
          <Slider
            id={`${ids}-y`}
            label="Vertical position"
            value={crop.y}
            disabled={freeY <= 0}
            onChange={(y) => {
              setCrop((c) => ({ ...c, y }));
            }}
          />
        </div>
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={upload.isPending}
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            type="button"
            disabled={upload.isPending}
            aria-busy={upload.isPending}
            onClick={() => {
              upload.mutate();
            }}
          >
            {upload.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            {upload.isPending ? "Uploading" : "Use this photo"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Slider({
  id,
  label,
  value,
  min = 0,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min?: number;
  disabled?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id}>{label}</Label>
      <input
        id={id}
        type="range"
        min={min}
        max={1}
        step={0.01}
        value={value}
        disabled={disabled}
        onChange={(event) => {
          onChange(Number(event.target.value));
        }}
        className="w-full accent-primary disabled:opacity-50"
      />
    </div>
  );
}
