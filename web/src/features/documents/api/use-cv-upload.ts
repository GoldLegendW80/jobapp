import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  confirmDocument,
  documentsKey,
  failureSentence as uploadFailure,
  listDocuments,
  uploadDocument,
} from "@/features/ingestion/api/documents-api";

import {
  baseCvsKey,
  createBaseCv,
  listBaseCvs,
  patchBaseCv,
  type BaseCvs,
} from "./base-cvs-api";
import { failureSentence } from "./documents-api";
import { pollInterval } from "../helpers/labels";
import {
  isCvFile,
  isRead,
  NOT_A_CV,
  READING_POLL_MS,
} from "../helpers/upload-cv";
import { useFollowProfileRun } from "./use-follow-profile-run";

/**
 * An uploaded CV on its way to a base CV: the file is sent to ingestion, which reads its text;
 * once read, it is converted, once, with no consent asked (owner, 2026-11-09). `problem` is the
 * sentence of the last failure; a new file starts over.
 */
export function useCvUpload() {
  const client = useQueryClient();
  const [problem, setProblem] = useState<string>();
  const [uploadId, setUploadId] = useState<string>();
  const [baseId, setBaseId] = useState<string>();
  /** The file whose conversion was asked for: once per upload. */
  const started = useRef<string>(undefined);

  const profileRun = useFollowProfileRun();
  const files = useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
    enabled: uploadId !== undefined && baseId === undefined,
    refetchInterval: (current) =>
      current.state.data?.items.find((one) => one.id === uploadId)?.status ===
      "pending"
        ? READING_POLL_MS
        : false,
  });
  const cvs = useQuery({
    queryKey: baseCvsKey,
    queryFn: ({ signal }) => listBaseCvs(signal),
    enabled: baseId !== undefined,
    refetchInterval: (current) => {
      const cv = current.state.data?.base_cvs.find((one) => one.id === baseId);
      return cv === undefined
        ? false
        : pollInterval(cv.render_state, cv.generation);
    },
  });

  const send = useMutation({
    mutationFn: (file: File) => uploadDocument(file, { forBaseCv: true }),
    onSuccess: (stored) => {
      setUploadId(stored.id);
      void client.invalidateQueries({ queryKey: documentsKey });
    },
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });
  const convert = useMutation({
    mutationFn: (id: string) =>
      createBaseCv({ from: "upload", ingestion_document_id: id }),
    onSuccess: (made) => {
      setBaseId(made.id);
      client.setQueryData<BaseCvs>(baseCvsKey, (old) =>
        old === undefined ? old : { ...old, base_cvs: [...old.base_cvs, made] },
      );
      void client.invalidateQueries({ queryKey: baseCvsKey });
    },
    onError: (error) => {
      setProblem(failureSentence(error));
    },
  });
  const keep = useMutation({
    mutationFn: confirmDocument,
    onSuccess: () => client.invalidateQueries({ queryKey: documentsKey }),
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });
  const makeDefault = useMutation({
    mutationFn: (id: string) => patchBaseCv(id, { default: true }),
    onSuccess: () => client.invalidateQueries({ queryKey: baseCvsKey }),
    onError: (error) => {
      setProblem(failureSentence(error));
    },
  });

  /** A file chosen or dropped: the panel starts over with it. */
  const take = (file: File | undefined) => {
    if (file === undefined) return;
    setProblem(undefined);
    setUploadId(undefined);
    setBaseId(undefined);
    if (!isCvFile(file.name)) {
      setProblem(NOT_A_CV);
      return;
    }
    send.mutate(file);
  };

  const file = files.data?.items.find((one) => one.id === uploadId);
  const cv = cvs.data?.base_cvs.find((one) => one.id === baseId);
  const read = isRead(file);
  const convertNow = convert.mutate;
  // The text is read: convert it, once.
  useEffect(() => {
    if (!read || uploadId === undefined) return;
    if (started.current === uploadId) return;
    started.current = uploadId;
    convertNow(uploadId);
  }, [read, uploadId, convertNow]);

  return {
    problem,
    uploadId,
    baseId,
    profileRun,
    file,
    cv,
    send,
    convert,
    keep,
    makeDefault,
    take,
    busy: send.isPending || convert.isPending,
  };
}
