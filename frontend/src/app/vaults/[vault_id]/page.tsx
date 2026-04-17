"use client";

import { useParams, useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  Alert,
  Badge,
  Button,
  ButtonLink,
  Card,
  Input,
  Text,
  useCatTheme,
} from "@/components/catmagui";
import BrandLogo from "@/components/BrandLogo";
import { buildAuthHeaders, getApiUrl, getErrorDetail, isUnauthorizedStatus } from "@/lib/api";
import { clearAuthSession, getAuthEmail, getAuthToken } from "@/lib/auth";

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

type DownloadResponse = {
  download_url?: string;
  expires_at?: string;
};

type VaultStatus = "active" | "grace_period" | "delivery_initiated" | "delivered" | "disabled";

const VAULT_STATUS_OPTIONS: VaultStatus[] = [
  "active",
  "grace_period",
  "delivery_initiated",
  "delivered",
  "disabled",
];

const EMAIL_REGEX =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;

function normalizeVaultId(rawVaultId: string | string[] | undefined): string {
  if (Array.isArray(rawVaultId)) {
    return rawVaultId[0] ?? "";
  }
  return rawVaultId ?? "";
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
  const t = useCatTheme();
  const router = useRouter();
  const params = useParams<{ vault_id?: string | string[] }>();
  const vaultId = useMemo(() => normalizeVaultId(params?.vault_id), [params]);
  const apiUrl = useMemo(() => getApiUrl(), []);

  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [signedInEmail, setSignedInEmail] = useState<string | null>(null);

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

  const [isDeletingRecipient, setIsDeletingRecipient] = useState<string | null>(null);
  const [isDeletingFileId, setIsDeletingFileId] = useState<string | null>(null);

  const [editableName, setEditableName] = useState("");
  const [editableGracePeriod, setEditableGracePeriod] = useState(30);
  const [editableStatus, setEditableStatus] = useState<VaultStatus>("active");
  const [isUpdatingVault, setIsUpdatingVault] = useState(false);
  const [updateMessage, setUpdateMessage] = useState<string | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);

  const [isDeletingVault, setIsDeletingVault] = useState(false);

  const authRedirectPath = useMemo(() => {
    if (!vaultId) {
      return "/";
    }
    return `/vaults/${encodeURIComponent(vaultId)}`;
  }, [vaultId]);

  const handleUnauthorized = useCallback(() => {
    clearAuthSession();
    setAuthToken(null);
    router.replace(`/auth?next=${encodeURIComponent(authRedirectPath)}`);
  }, [authRedirectPath, router]);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      handleUnauthorized();
      setIsCheckingAuth(false);
      return;
    }

    setAuthToken(token);
    setSignedInEmail(getAuthEmail());
    setIsCheckingAuth(false);
  }, [handleUnauthorized]);

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

      if (!authToken) {
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
          fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}`, {
            headers: buildAuthHeaders(authToken, false),
          }),
          fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files`, {
            headers: buildAuthHeaders(authToken, false),
          }),
        ]);

        if (isUnauthorizedStatus(vaultResponse.status) || isUnauthorizedStatus(filesResponse.status)) {
          handleUnauthorized();
          return;
        }

        if (!vaultResponse.ok) {
          const message = await getErrorDetail(vaultResponse, "Failed to fetch vault details.");
          throw new Error(message);
        }
        if (!filesResponse.ok) {
          const message = await getErrorDetail(filesResponse, "Failed to fetch vault files.");
          throw new Error(message);
        }

        const vaultPayload = (await vaultResponse.json()) as VaultDetail;
        const filesPayload = (await filesResponse.json()) as VaultFilesResponse;

        setVault(vaultPayload);
        setEditableName(vaultPayload.name || "");
        setEditableGracePeriod(Number(vaultPayload.grace_period_days || 1));
        if (VAULT_STATUS_OPTIONS.includes(vaultPayload.status as VaultStatus)) {
          setEditableStatus(vaultPayload.status as VaultStatus);
        }
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
    [apiUrl, authToken, handleUnauthorized, vaultId]
  );

  useEffect(() => {
    if (!isCheckingAuth && authToken) {
      void fetchVaultData(true);
    }
  }, [authToken, fetchVaultData, isCheckingAuth]);

  const handleSignOut = useCallback(() => {
    clearAuthSession();
    setAuthToken(null);
    router.replace(`/auth?next=${encodeURIComponent(authRedirectPath)}`);
  }, [authRedirectPath, router]);

  const handleAddRecipient = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setRecipientMessage(null);
    setRecipientError(null);

    const email = recipientEmail.trim().toLowerCase();
    if (!EMAIL_REGEX.test(email)) {
      setRecipientError("Please provide a valid email address.");
      return;
    }
    if (!apiUrl || !vaultId || !authToken) {
      setRecipientError("API URL or vault identifier is missing.");
      return;
    }

    setIsAddingRecipient(true);
    try {
      const response = await fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}/recipients`, {
        method: "POST",
        headers: buildAuthHeaders(authToken, true),
        body: JSON.stringify({ email }),
      });

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to add recipient.");
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

  const handleDeleteRecipient = async (email: string) => {
    setRecipientMessage(null);
    setRecipientError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setRecipientError("API URL or vault identifier is missing.");
      return;
    }

    setIsDeletingRecipient(email);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/recipients/${encodeURIComponent(email)}`,
        {
          method: "DELETE",
          headers: buildAuthHeaders(authToken, false),
        }
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to delete recipient.");
        throw new Error(message);
      }

      setRecipientMessage("Recipient removed successfully.");
      await fetchVaultData(false);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while deleting recipient.";
      setRecipientError(message);
    } finally {
      setIsDeletingRecipient(null);
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
    if (!apiUrl || !vaultId || !authToken) {
      setUploadError("API URL or vault identifier is missing.");
      return;
    }

    setIsUploadingFile(true);
    try {
      const formData = new FormData();
      formData.append("file", selectedFile);

      const response = await fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files`, {
        method: "POST",
        headers: buildAuthHeaders(authToken, false),
        body: formData,
      });

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to upload file.");
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
    if (!apiUrl || !vaultId || !authToken) {
      setDownloadError("API URL or vault identifier is missing.");
      return;
    }

    setDownloadingFileId(fileId);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files/${encodeURIComponent(fileId)}/download`,
        {
          headers: buildAuthHeaders(authToken, false),
        }
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to generate download URL.");
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

  const handleDeleteFile = async (fileId: string) => {
    setDownloadError(null);
    if (!apiUrl || !vaultId || !authToken) {
      setDownloadError("API URL or vault identifier is missing.");
      return;
    }

    setIsDeletingFileId(fileId);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files/${encodeURIComponent(fileId)}`,
        {
          method: "DELETE",
          headers: buildAuthHeaders(authToken, false),
        }
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to delete file.");
        throw new Error(message);
      }

      await fetchVaultData(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected file delete error.";
      setDownloadError(message);
    } finally {
      setIsDeletingFileId(null);
    }
  };

  const handleUpdateVault = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setUpdateMessage(null);
    setUpdateError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setUpdateError("API URL or vault identifier is missing.");
      return;
    }

    const normalizedName = editableName.trim();
    if (!normalizedName) {
      setUpdateError("Vault name is required.");
      return;
    }

    if (!Number.isFinite(editableGracePeriod) || editableGracePeriod < 1) {
      setUpdateError("Grace period must be at least 1 day.");
      return;
    }

    setIsUpdatingVault(true);
    try {
      const response = await fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}`, {
        method: "PATCH",
        headers: buildAuthHeaders(authToken, true),
        body: JSON.stringify({
          name: normalizedName,
          grace_period_days: Number(editableGracePeriod),
          status: editableStatus,
        }),
      });

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to update vault.");
        throw new Error(message);
      }

      setUpdateMessage("Vault settings updated.");
      await fetchVaultData(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected vault update error.";
      setUpdateError(message);
    } finally {
      setIsUpdatingVault(false);
    }
  };

  const handleDeleteVault = async () => {
    setUpdateMessage(null);
    setUpdateError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setUpdateError("API URL or vault identifier is missing.");
      return;
    }

    if (!window.confirm("Delete this vault and all uploaded files?")) {
      return;
    }

    setIsDeletingVault(true);
    try {
      const response = await fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}`, {
        method: "DELETE",
        headers: buildAuthHeaders(authToken, false),
      });

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to delete vault.");
        throw new Error(message);
      }

      router.replace("/");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected vault delete error.";
      setUpdateError(message);
    } finally {
      setIsDeletingVault(false);
    }
  };

  const mainBackground = t.isDark
    ? "radial-gradient(circle at 15% 10%, rgba(216, 27, 96, 0.14), transparent 35%), radial-gradient(circle at 80% 8%, rgba(80, 80, 90, 0.32), transparent 30%), linear-gradient(180deg, #050505 0%, #09090B 60%, #050505 100%)"
    : "radial-gradient(circle at 15% 10%, rgba(216, 27, 96, 0.1), transparent 38%), radial-gradient(circle at 84% 10%, rgba(24, 24, 27, 0.06), transparent 35%), linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 55%, #FFFFFF 100%)";

  if (isCheckingAuth) {
    return (
      <main
        style={{
          minHeight: "100vh",
          background: mainBackground,
          color: t.colors.text.primary,
          padding: `${t.space.xl}px ${t.space.m}px`,
          fontFamily: "var(--font-geist-sans), sans-serif",
        }}
      >
        <div style={{ margin: "0 auto", width: "100%", maxWidth: 1180 }}>
          <Card variant="elevated">
            <Alert variant="info" message="Checking session..." />
          </Card>
        </div>
      </main>
    );
  }

  if (!authToken) {
    return null;
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        background: mainBackground,
        color: t.colors.text.primary,
        padding: `${t.space.xl}px ${t.space.m}px`,
        fontFamily: "var(--font-geist-sans), sans-serif",
      }}
    >
      <div style={{ margin: "0 auto", width: "100%", maxWidth: 1180, display: "grid", gap: t.space.m }}>
        <BrandLogo marginBottom={t.space.xxs} />

        <Card variant="elevated" style={{ gap: t.space.s }}>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              justifyContent: "space-between",
              alignItems: "flex-end",
              gap: t.space.s,
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: t.space.xs }}>
              <Text variant="h2">{vault ? vault.name : "Vault Details"}</Text>
              <Text variant="bodySmall" color="secondary">
                Vault ID: {vaultId || "Unavailable"}
              </Text>
              {signedInEmail ? (
                <Text variant="caption" color="muted">
                  Signed in as {signedInEmail}
                </Text>
              ) : null}
            </div>

            <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
              <ButtonLink href="/" variant="Primary">
                Back to Dashboard
              </ButtonLink>
              <Button
                type="button"
                onClick={() => void fetchVaultData(false)}
                disabled={isLoading || isRefreshing}
                variant="SolidPrimary"
              >
                {isRefreshing ? "Refreshing..." : "Refresh"}
              </Button>
              <Button type="button" onClick={handleSignOut} variant="Destructive">
                Sign Out
              </Button>
            </div>
          </div>

          {vault ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: t.space.xs,
              }}
            >
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Status</Text>
                <Text variant="label">{vault.status}</Text>
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Grace Period</Text>
                <Text variant="label">{vault.grace_period_days} days</Text>
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Recipients</Text>
                <Text variant="label">{vault.recipients.length}</Text>
              </Card>
            </div>
          ) : null}
        </Card>

        {isLoading ? <Alert variant="info" message="Loading vault details..." /> : null}

        {!isLoading && pageError ? (
          <Card variant="secondary" style={{ gap: t.space.s }}>
            <Alert variant="error" message={pageError} />
            <div>
              <Button type="button" onClick={() => void fetchVaultData(true)} variant="Primary">
                Retry
              </Button>
            </div>
          </Card>
        ) : null}

        {!isLoading && !pageError ? (
          <section
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
              gap: t.space.m,
              alignItems: "start",
            }}
          >
            <div style={{ display: "grid", gap: t.space.m }}>
              <Card variant="elevated" style={{ gap: t.space.s }}>
                <Text variant="h3">Vault Settings</Text>
                <form
                  onSubmit={handleUpdateVault}
                  style={{ display: "flex", flexDirection: "column", gap: t.space.s }}
                >
                  <Input
                    id="vault-name"
                    label="Vault Name"
                    value={editableName}
                    onChange={(event) => setEditableName(event.target.value)}
                    required
                  />

                  <Input
                    id="vault-grace"
                    type="number"
                    min={1}
                    max={3650}
                    label="Grace Period (days)"
                    value={editableGracePeriod}
                    onChange={(event) => setEditableGracePeriod(Number(event.target.value))}
                    required
                  />

                  <div style={{ display: "flex", flexDirection: "column", gap: t.space.xs }}>
                    <Text variant="label" color="secondary">
                      Status
                    </Text>
                    <select
                      value={editableStatus}
                      onChange={(event) => setEditableStatus(event.target.value as VaultStatus)}
                      style={{
                        width: "100%",
                        border: `1px solid ${t.colors.components.input.border}`,
                        backgroundColor: t.colors.components.input.bg,
                        color: t.colors.text.primary,
                        borderRadius: t.radius.full,
                        padding: `${t.space.s}px ${t.space.m}px`,
                        fontFamily: "var(--font-geist-sans), sans-serif",
                        fontSize: t.typography.body.fontSize,
                        lineHeight: String(t.typography.body.lineHeight),
                        outline: "none",
                      }}
                    >
                      {VAULT_STATUS_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </div>

                  <Button
                    type="submit"
                    size="full"
                    variant="SolidPrimary"
                    disabled={isUpdatingVault}
                  >
                    {isUpdatingVault ? "Updating Vault..." : "Save Vault Settings"}
                  </Button>
                </form>

                <Button
                  type="button"
                  variant="Destructive"
                  onClick={() => void handleDeleteVault()}
                  disabled={isDeletingVault}
                >
                  {isDeletingVault ? "Deleting Vault..." : "Delete Vault"}
                </Button>

                {updateError ? <Alert variant="error" message={updateError} /> : null}
                {updateMessage ? <Alert variant="success" message={updateMessage} /> : null}
              </Card>

              <Card variant="elevated" style={{ gap: t.space.s }}>
                <Text variant="h3">Recipients</Text>
                <Text variant="bodySmall" color="secondary">
                  Add recipients who can receive the vault when delivery is initiated.
                </Text>

                <form
                  onSubmit={handleAddRecipient}
                  style={{ display: "flex", flexDirection: "column", gap: t.space.s }}
                >
                  <Input
                    type="email"
                    value={recipientEmail}
                    onChange={(event) => setRecipientEmail(event.target.value)}
                    placeholder="recipient@example.com"
                    required
                  />
                  <Button
                    type="submit"
                    size="full"
                    variant="SolidPrimary"
                    disabled={isAddingRecipient}
                  >
                    {isAddingRecipient ? "Adding Recipient..." : "Add Recipient"}
                  </Button>
                </form>

                {recipientError ? <Alert variant="error" message={recipientError} /> : null}
                {recipientMessage ? <Alert variant="success" message={recipientMessage} /> : null}

                <div style={{ display: "grid", gap: t.space.xs }}>
                  {vault?.recipients.length ? (
                    vault.recipients.map((recipient) => (
                      <Card key={recipient} variant="secondary" style={{ padding: t.space.s, gap: t.space.xs }}>
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            gap: t.space.xs,
                            flexWrap: "wrap",
                          }}
                        >
                          <Text variant="bodySmall">{recipient}</Text>
                          <Button
                            type="button"
                            size="default"
                            variant="Destructive"
                            disabled={isDeletingRecipient === recipient}
                            onClick={() => void handleDeleteRecipient(recipient)}
                          >
                            {isDeletingRecipient === recipient ? "Removing..." : "Remove"}
                          </Button>
                        </div>
                      </Card>
                    ))
                  ) : (
                    <Alert variant="info" message="No recipients configured yet." />
                  )}
                </div>
              </Card>

              <Card variant="elevated" style={{ gap: t.space.s }}>
                <Text variant="h3">Upload File</Text>
                <Text variant="bodySmall" color="secondary">
                  Attach new files to this vault using secure upload.
                </Text>

                <form
                  onSubmit={handleFileUpload}
                  style={{ display: "flex", flexDirection: "column", gap: t.space.s }}
                >
                  <input
                    type="file"
                    onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
                    style={{
                      width: "100%",
                      border: `1px solid ${t.colors.components.input.border}`,
                      borderRadius: t.radius.l,
                      backgroundColor: t.colors.components.input.bg,
                      color: t.colors.text.secondary,
                      padding: `${t.space.s}px ${t.space.s}px`,
                      fontFamily: "var(--font-geist-sans), sans-serif",
                    }}
                  />
                  <Button
                    type="submit"
                    size="full"
                    variant="SolidPrimary"
                    disabled={isUploadingFile}
                  >
                    {isUploadingFile ? "Uploading..." : "Upload File"}
                  </Button>
                </form>

                {uploadError ? <Alert variant="error" message={uploadError} /> : null}
                {uploadMessage ? <Alert variant="success" message={uploadMessage} /> : null}
              </Card>
            </div>

            <Card variant="elevated" style={{ gap: t.space.s }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: t.space.xs,
                }}
              >
                <Text variant="h3">Attached Files</Text>
                <Badge label={`${files.length} files`} size="sm" outlineOnly />
              </div>

              {downloadError ? <Alert variant="error" message={downloadError} /> : null}

              {files.length === 0 ? (
                <Alert variant="info" message="No files uploaded yet." />
              ) : (
                <div style={{ display: "grid", gap: t.space.s }}>
                  {files.map((fileItem) => (
                    <Card key={fileItem.id} variant="secondary" style={{ padding: t.space.m, gap: t.space.s }}>
                      <div
                        style={{
                          display: "flex",
                          flexWrap: "wrap",
                          justifyContent: "space-between",
                          alignItems: "flex-start",
                          gap: t.space.s,
                        }}
                      >
                        <div style={{ display: "flex", flexDirection: "column", gap: t.space.xxs }}>
                          <Text variant="label">{fileItem.file_name}</Text>
                          <Text variant="caption" color="muted">File ID: {fileItem.id}</Text>
                        </div>
                        <Button
                          type="button"
                          onClick={() => void handleDownload(fileItem.id)}
                          disabled={downloadingFileId === fileItem.id}
                          variant="Primary"
                        >
                          {downloadingFileId === fileItem.id ? "Preparing..." : "Download"}
                        </Button>
                        <Button
                          type="button"
                          onClick={() => void handleDeleteFile(fileItem.id)}
                          disabled={isDeletingFileId === fileItem.id}
                          variant="Destructive"
                        >
                          {isDeletingFileId === fileItem.id ? "Removing..." : "Delete"}
                        </Button>
                      </div>

                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
                          gap: t.space.xs,
                        }}
                      >
                        <Text variant="caption" color="secondary">
                          Size: {formatBytes(fileItem.size_bytes)}
                        </Text>
                        <Text variant="caption" color="secondary">
                          Type: {fileItem.content_type || "Unknown"}
                        </Text>
                        <Text variant="caption" color="secondary">
                          Uploaded: {fileItem.uploaded_at || "Unknown"}
                        </Text>
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            </Card>
          </section>
        ) : null}
      </div>
    </main>
  );
}
