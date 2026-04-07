"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

type VaultDetail = {
  id: string;
  user_id: string;
  name: string;
  grace_period_days: number;
  status: string;
  recipients: string[];
};

type VaultFile = {
  id: string;
  file_name: string;
  blob_name?: string;
  content_type?: string | null;
  size_bytes?: number | null;
  uploaded_at?: string;
};

type VaultFilesResponse = {
  vault_id: string;
  files: VaultFile[];
};

type ErrorPayload = {
  detail?: string;
};

type DownloadResponse = {
  download_url?: string;
  expires_at?: string;
};

const EMAIL_REGEX =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;

function normalizeVaultId(rawVaultId: string | string[] | undefined): string {
  if (Array.isArray(rawVaultId)) {
    return rawVaultId[0] ?? "";
  }
  return rawVaultId ?? "";
}

async function getErrorMessage(response: Response, fallbackMessage: string): Promise<string> {
  try {
    const payload = (await response.json()) as ErrorPayload;
    if (payload.detail) {
      return payload.detail;
    }
  } catch {
    return `${fallbackMessage} HTTP ${response.status}.`;
  }
  return fallbackMessage;
}

function formatBytes(sizeInBytes: number | null | undefined): string {
  if (typeof sizeInBytes !== "number" || Number.isNaN(sizeInBytes) || sizeInBytes < 0) {
    return "Unknown";
  }
  if (sizeInBytes < 1024) {
    return `${sizeInBytes} B`;
  }

  const units = ["KB", "MB", "GB", "TB"];
  let size = sizeInBytes / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`;
}

export default function VaultDetailsPage() {
  const params = useParams<{ vault_id?: string | string[] }>();
  const vaultId = useMemo(() => normalizeVaultId(params?.vault_id), [params]);
  const apiUrl = useMemo(
    () => (process.env.NEXT_PUBLIC_API_URL || "").trim().replace(/\/$/, ""),
    []
  );

  const [vault, setVault] = useState<VaultDetail | null>(null);
  const [files, setFiles] = useState<VaultFile[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);

  const [recipientEmail, setRecipientEmail] = useState("");
  const [isAddingRecipient, setIsAddingRecipient] = useState(false);
  const [recipientMessage, setRecipientMessage] = useState<string | null>(null);
  const [recipientError, setRecipientError] = useState<string | null>(null);

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploadingFile, setIsUploadingFile] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [downloadingFileId, setDownloadingFileId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const fetchVaultData = useCallback(
    async (displayFullLoading: boolean) => {
      if (!apiUrl) {
        setPageError("NEXT_PUBLIC_API_URL is not configured.");
        setIsLoading(false);
        setIsRefreshing(false);
        return;
      }

      if (!vaultId) {
        setPageError("Vault identifier is missing in the URL.");
        setIsLoading(false);
        setIsRefreshing(false);
        return;
      }

      if (displayFullLoading) {
        setIsLoading(true);
      } else {
        setIsRefreshing(true);
      }
      setPageError(null);

      try {
        const [vaultResponse, filesResponse] = await Promise.all([
          fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}`),
          fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files`),
        ]);

        if (!vaultResponse.ok) {
          const message = await getErrorMessage(vaultResponse, "Failed to fetch vault details.");
          throw new Error(message);
        }
        if (!filesResponse.ok) {
          const message = await getErrorMessage(filesResponse, "Failed to fetch vault files.");
          throw new Error(message);
        }

        const vaultPayload = (await vaultResponse.json()) as VaultDetail;
        const filesPayload = (await filesResponse.json()) as VaultFilesResponse;

        setVault(vaultPayload);
        setFiles(Array.isArray(filesPayload.files) ? filesPayload.files : []);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Unexpected error while loading vault details.";
        setPageError(message);
      } finally {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [apiUrl, vaultId]
  );

  useEffect(() => {
    void fetchVaultData(true);
  }, [fetchVaultData]);

  const handleAddRecipient = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setRecipientMessage(null);
    setRecipientError(null);

    const email = recipientEmail.trim().toLowerCase();
    if (!EMAIL_REGEX.test(email)) {
      setRecipientError("Please provide a valid email address.");
      return;
    }
    if (!apiUrl || !vaultId) {
      setRecipientError("API URL or vault identifier is missing.");
      return;
    }

    setIsAddingRecipient(true);
    try {
      const response = await fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}/recipients`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email }),
      });

      if (!response.ok) {
        const message = await getErrorMessage(response, "Failed to add recipient.");
        throw new Error(message);
      }

      setRecipientEmail("");
      setRecipientMessage("Recipient added successfully.");
      await fetchVaultData(false);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while adding recipient.";
      setRecipientError(message);
    } finally {
      setIsAddingRecipient(false);
    }
  };

  const handleFileUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setUploadMessage(null);
    setUploadError(null);

    if (!selectedFile) {
      setUploadError("Please choose a file before uploading.");
      return;
    }
    if (!apiUrl || !vaultId) {
      setUploadError("API URL or vault identifier is missing.");
      return;
    }

    setIsUploadingFile(true);
    try {
      const formData = new FormData();
      formData.append("file", selectedFile);

      const response = await fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const message = await getErrorMessage(response, "Failed to upload file.");
        throw new Error(message);
      }

      setSelectedFile(null);
      setUploadMessage("File uploaded successfully.");
      await fetchVaultData(false);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while uploading file.";
      setUploadError(message);
    } finally {
      setIsUploadingFile(false);
    }
  };

  const handleDownload = async (fileId: string) => {
    setDownloadError(null);
    if (!apiUrl || !vaultId) {
      setDownloadError("API URL or vault identifier is missing.");
      return;
    }

    setDownloadingFileId(fileId);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files/${encodeURIComponent(fileId)}/download`
      );

      if (!response.ok) {
        const message = await getErrorMessage(response, "Failed to generate download URL.");
        throw new Error(message);
      }

      const payload = (await response.json()) as DownloadResponse;
      if (!payload.download_url) {
        throw new Error("Download URL was not returned by the API.");
      }

      window.open(payload.download_url, "_blank", "noopener,noreferrer");
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while preparing download.";
      setDownloadError(message);
    } finally {
      setDownloadingFileId(null);
    }
  };

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(20,184,166,0.18),transparent_40%),radial-gradient(circle_at_85%_15%,rgba(14,165,233,0.15),transparent_35%),linear-gradient(180deg,#f8fafc_0%,#ecfeff_45%,#f8fafc_100%)] px-4 py-10 font-['Space_Grotesk',sans-serif] text-slate-900 sm:px-8 lg:px-12">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <header className="rounded-2xl border border-slate-200 bg-white/90 p-6 shadow-xl shadow-slate-200/70 backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="inline-block rounded-full border border-cyan-200 bg-cyan-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">
                Last Writes Vault
              </p>
              <h1 className="mt-3 font-['Fraunces',serif] text-3xl font-semibold text-slate-900 sm:text-4xl">
                {vault ? vault.name : "Vault Details"}
              </h1>
              <p className="mt-2 text-sm text-slate-600">Vault ID: {vaultId || "Unavailable"}</p>
            </div>

            <div className="flex items-center gap-2">
              <Link
                href="/"
                className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-cyan-400 hover:text-cyan-700"
              >
                Back to Dashboard
              </Link>
              <button
                type="button"
                onClick={() => void fetchVaultData(false)}
                disabled={isLoading || isRefreshing}
                className="rounded-xl bg-linear-to-r from-teal-600 to-cyan-600 px-4 py-2 text-sm font-semibold text-white transition hover:from-teal-500 hover:to-cyan-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isRefreshing ? "Refreshing..." : "Refresh"}
              </button>
            </div>
          </div>

          {vault ? (
            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                <p className="text-slate-500">Status</p>
                <p className="font-semibold text-slate-800">{vault.status}</p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                <p className="text-slate-500">Grace Period</p>
                <p className="font-semibold text-slate-800">{vault.grace_period_days} days</p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                <p className="text-slate-500">Recipients</p>
                <p className="font-semibold text-slate-800">{vault.recipients.length}</p>
              </div>
            </div>
          ) : null}
        </header>

        {isLoading ? (
          <section className="rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600 shadow-lg">
            Loading vault details...
          </section>
        ) : null}

        {!isLoading && pageError ? (
          <section className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-lg">
            <p>{pageError}</p>
            <button
              type="button"
              onClick={() => void fetchVaultData(true)}
              className="mt-4 rounded-lg border border-rose-300 bg-white px-3 py-2 font-semibold text-rose-700 transition hover:bg-rose-100"
            >
              Retry
            </button>
          </section>
        ) : null}

        {!isLoading && !pageError ? (
          <section className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(320px,380px)_minmax(0,1fr)]">
            <div className="space-y-6">
              <article className="rounded-2xl border border-slate-200 bg-white/90 p-6 shadow-xl shadow-slate-200/70 backdrop-blur">
                <h2 className="font-['Fraunces',serif] text-2xl font-semibold text-slate-900">
                  Recipients
                </h2>
                <p className="mt-2 text-sm text-slate-600">
                  Add recipients who can receive the vault when delivery is initiated.
                </p>

                <form onSubmit={handleAddRecipient} className="mt-5 space-y-3">
                  <input
                    type="email"
                    value={recipientEmail}
                    onChange={(event) => setRecipientEmail(event.target.value)}
                    placeholder="recipient@example.com"
                    className="w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-slate-900 outline-none transition focus:border-teal-500 focus:shadow-[0_0_0_4px_rgba(20,184,166,0.15)]"
                    required
                  />
                  <button
                    type="submit"
                    disabled={isAddingRecipient}
                    className="w-full rounded-xl bg-linear-to-r from-teal-600 to-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:from-teal-500 hover:to-cyan-500 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isAddingRecipient ? "Adding Recipient..." : "Add Recipient"}
                  </button>
                </form>

                {recipientError ? (
                  <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                    {recipientError}
                  </p>
                ) : null}
                {recipientMessage ? (
                  <p className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                    {recipientMessage}
                  </p>
                ) : null}

                <div className="mt-5 space-y-2">
                  {vault?.recipients.length ? (
                    vault.recipients.map((recipient) => (
                      <p
                        key={recipient}
                        className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
                      >
                        {recipient}
                      </p>
                    ))
                  ) : (
                    <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                      No recipients configured yet.
                    </p>
                  )}
                </div>
              </article>

              <article className="rounded-2xl border border-slate-200 bg-white/90 p-6 shadow-xl shadow-slate-200/70 backdrop-blur">
                <h2 className="font-['Fraunces',serif] text-2xl font-semibold text-slate-900">
                  Upload File
                </h2>
                <p className="mt-2 text-sm text-slate-600">
                  Attach new files to this vault using secure upload.
                </p>

                <form onSubmit={handleFileUpload} className="mt-5 space-y-3">
                  <input
                    type="file"
                    onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
                    className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-100 file:px-3 file:py-1.5 file:font-semibold file:text-cyan-700"
                  />
                  <button
                    type="submit"
                    disabled={isUploadingFile}
                    className="w-full rounded-xl bg-linear-to-r from-teal-600 to-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:from-teal-500 hover:to-cyan-500 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isUploadingFile ? "Uploading..." : "Upload File"}
                  </button>
                </form>

                {uploadError ? (
                  <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                    {uploadError}
                  </p>
                ) : null}
                {uploadMessage ? (
                  <p className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                    {uploadMessage}
                  </p>
                ) : null}
              </article>
            </div>

            <article className="rounded-2xl border border-slate-200 bg-white/90 p-6 shadow-xl shadow-slate-200/70 backdrop-blur">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="font-['Fraunces',serif] text-2xl font-semibold text-slate-900">
                  Attached Files
                </h2>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
                  {files.length} files
                </span>
              </div>

              {downloadError ? (
                <p className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {downloadError}
                </p>
              ) : null}

              {files.length === 0 ? (
                <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                  No files uploaded yet.
                </p>
              ) : (
                <div className="space-y-3">
                  {files.map((fileItem) => (
                    <div
                      key={fileItem.id}
                      className="rounded-xl border border-slate-200 bg-white p-4 transition hover:border-cyan-300"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-slate-900">{fileItem.file_name}</p>
                          <p className="mt-1 text-xs text-slate-500">File ID: {fileItem.id}</p>
                        </div>
                        <button
                          type="button"
                          onClick={() => void handleDownload(fileItem.id)}
                          disabled={downloadingFileId === fileItem.id}
                          className="rounded-lg border border-cyan-300 bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {downloadingFileId === fileItem.id ? "Preparing..." : "Download"}
                        </button>
                      </div>

                      <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-slate-600 sm:grid-cols-3">
                        <p>
                          Size: <span className="font-medium text-slate-800">{formatBytes(fileItem.size_bytes)}</span>
                        </p>
                        <p>
                          Type: <span className="font-medium text-slate-800">{fileItem.content_type || "Unknown"}</span>
                        </p>
                        <p>
                          Uploaded: <span className="font-medium text-slate-800">{fileItem.uploaded_at || "Unknown"}</span>
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </article>
          </section>
        ) : null}
      </div>
    </main>
  );
}
