import { useMutation, useQueryClient } from "@tanstack/react-query";

import { deletePhoto, uploadPhoto } from "./photo-api";
import { profileKey, type Profile } from "./profile-api";
import { cropToBlob, type Rect } from "../helpers/crop";

/** Writes the stored photo into the cached profile: its etag, or null once removed. */
function useSetPhoto() {
  const client = useQueryClient();
  return (etag: string | null) => {
    client.setQueryData<Profile>(profileKey, (old) =>
      old ? { ...old, has_photo: etag !== null, photo_etag: etag } : old,
    );
  };
}

/** Removes the photo; the cached profile loses it once the api agrees. */
export function useRemovePhoto(on: {
  onRemoved: () => void;
  onError: (error: Error) => void;
}) {
  const setPhoto = useSetPhoto();
  return useMutation({
    mutationFn: deletePhoto,
    onSuccess: () => {
      setPhoto(null);
      on.onRemoved();
    },
    onError: on.onError,
  });
}

/** Crops the chosen image to its square and uploads it; the cached profile takes its etag. */
export function useUploadPhoto(on: {
  onStart: () => void;
  onUploaded: () => void;
  onError: (error: Error) => void;
}) {
  const setPhoto = useSetPhoto();
  return useMutation({
    mutationFn: async ({
      image,
      rect,
      mime,
    }: {
      image: CanvasImageSource;
      rect: Rect;
      mime: "image/png" | "image/jpeg";
    }) => {
      const blob = await cropToBlob(image, rect, mime);
      return uploadPhoto(blob);
    },
    onMutate: () => {
      on.onStart();
    },
    onSuccess: (photo) => {
      setPhoto(photo.etag);
      on.onUploaded();
    },
    onError: on.onError,
  });
}
