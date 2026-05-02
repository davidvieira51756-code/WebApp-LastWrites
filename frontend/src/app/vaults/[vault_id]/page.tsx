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
import ThemeToggleButton from "@/components/ThemeToggleButton";
import { buildAuthHeaders, getApiUrl, getErrorDetail, isUnauthorizedStatus } from "@/lib/api";
import { clearAuthSession, getAuthEmail, getAuthToken } from "@/lib/auth";

type ActivationRequestItem = {
  recipient_email: string;
  requested_at: string;
  reason?: string | null;
};

type VaultRecipient = {
  email: string;
  can_activate: boolean;
};

type DeliveryPackage = {
  recipient_email: string;
  file_name: string;
  container_name?: string | null;
  blob_name?: string | null;
  delivered_at?: string | null;
};

type VaultDetail = {
  id: string;
  user_id: string;
  name: string;
  owner_message?: string | null;
  grace_period_days: number;
  grace_period_value?: number;
  grace_period_unit?: "days" | "hours";
  grace_period_hours?: number;
  status: string;
  recipients: VaultRecipient[];
  activation_threshold?: number;
  activation_requests?: ActivationRequestItem[];
  grace_period_started_at?: string | null;
  grace_period_expires_at?: string | null;
  last_check_in_at?: string | null;
  delivery_blob_name?: string | null;
  delivery_container_name?: string | null;
  delivery_file_name?: string | null;
  delivered_at?: string | null;
  delivery_error?: string | null;
  delivery_packages?: DeliveryPackage[];
};

type VaultFile = {
  id: string;
  file_name: string;
  blob_name?: string;
  content_type?: string | null;
  size_bytes?: number | null;
  ciphertext_size_bytes?: number | null;
  uploaded_at?: string;
  encrypted?: boolean;
  algorithm?: string | null;
  recipient_emails?: string[];
};

type VaultFilesResponse = {
  vault_id: string;
  files: VaultFile[];
};

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

function statusBadgeVariant(
  status: string,
): "default" | "success" | "warning" | "error" {
  const normalized = status.toLowerCase();
  if (normalized === "active") return "success";
  if (normalized === "pending_activation") return "warning";
  if (normalized === "grace_period") return "warning";
  if (
    normalized === "delivery_initiated" ||
    normalized === "delivered" ||
    normalized === "delivered_archived"
  ) return "error";
  if (normalized === "disabled") return "default";
  return "default";
}

function formatStatusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function formatIsoDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function getDownloadFileName(response: Response, fallbackName: string): string {
  const contentDisposition = response.headers.get("content-disposition") || "";
  const encodedMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encodedMatch?.[1]) {
    try {
      return decodeURIComponent(encodedMatch[1]);
    } catch {
      return encodedMatch[1];
    }
  }

  const plainMatch = contentDisposition.match(/filename=\"([^\"]+)\"/i);
  if (plainMatch?.[1]) {
    return plainMatch[1];
  }

  return fallbackName;
}

function buildDeliveryZipFallbackName(vault?: VaultDetail | null): string {
  if (!vault) {
    return "vault-delivery.zip";
  }

  return `${vault.id}-${vault.name}.zip`;
}

function triggerBrowserDownload(blob: Blob, fileName: string): void {
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}

function describeCountdown(expiresAt: string | null | undefined): string {
  if (!expiresAt) return "";
  const target = new Date(expiresAt).getTime();
  if (Number.isNaN(target)) return "";
  const now = Date.now();
  const diffMs = target - now;
  if (diffMs <= 0) return "Grace period already expired.";

  const totalSeconds = Math.floor(diffMs / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);

  if (days > 0) return `${days}d ${hours}h remaining`;
  if (hours > 0) return `${hours}h ${minutes}m remaining`;
  return `${minutes}m remaining`;
}

function formatGracePeriod(value?: number, unit?: "days" | "hours"): string {
  const normalizedValue = Number(value || 0);
  const normalizedUnit = unit === "hours" ? "hours" : "days";
  const suffix = normalizedValue === 1 ? normalizedUnit.slice(0, -1) : normalizedUnit;
  return `${normalizedValue} ${suffix}`;
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
  const [newRecipientCanActivate, setNewRecipientCanActivate] = useState(true);
  const [isAddingRecipient, setIsAddingRecipient] = useState(false);
  const [recipientMessage, setRecipientMessage] = useState<string | null>(null);
  const [recipientError, setRecipientError] = useState<string | null>(null);
  const [isUpdatingRecipientPermission, setIsUpdatingRecipientPermission] = useState<string | null>(null);

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedRecipientEmails, setSelectedRecipientEmails] = useState<string[]>([]);
  const [isUploadingFile, setIsUploadingFile] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [downloadingFileId, setDownloadingFileId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const [isDeletingRecipient, setIsDeletingRecipient] = useState<string | null>(null);
  const [isDeletingFileId, setIsDeletingFileId] = useState<string | null>(null);

  const [editableName, setEditableName] = useState("");
  const [editableOwnerMessage, setEditableOwnerMessage] = useState("");
  const [editableGracePeriod, setEditableGracePeriod] = useState(30);
  const [editableGracePeriodUnit, setEditableGracePeriodUnit] = useState<"days" | "hours">("days");
  const [editableThreshold, setEditableThreshold] = useState(1);
  const [isUpdatingVault, setIsUpdatingVault] = useState(false);
  const [updateMessage, setUpdateMessage] = useState<string | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);

  const [isCheckingIn, setIsCheckingIn] = useState(false);
  const [checkInMessage, setCheckInMessage] = useState<string | null>(null);
  const [checkInError, setCheckInError] = useState<string | null>(null);

  const [isDeletingVault, setIsDeletingVault] = useState(false);
  const [isDownloadingPackage, setIsDownloadingPackage] = useState(false);
  const [downloadingPackageRecipient, setDownloadingPackageRecipient] = useState<string | null>(null);

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
        setEditableOwnerMessage(vaultPayload.owner_message || "");
        setEditableGracePeriod(Number(vaultPayload.grace_period_value || vaultPayload.grace_period_days || 1));
        setEditableGracePeriodUnit(vaultPayload.grace_period_unit === "hours" ? "hours" : "days");
        setEditableThreshold(Number(vaultPayload.activation_threshold || 1));
        setFiles(Array.isArray(filesPayload.files) ? filesPayload.files : []);
        setSelectedRecipientEmails((currentSelection) => {
          const availableEmails = new Set((vaultPayload.recipients || []).map((recipient) => recipient.email));
          return currentSelection.filter((email) => availableEmails.has(email));
        });
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
        body: JSON.stringify({ email, can_activate: newRecipientCanActivate }),
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
      setNewRecipientCanActivate(true);
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
      setSelectedRecipientEmails((currentSelection) =>
        currentSelection.filter((currentEmail) => currentEmail !== email),
      );
      await fetchVaultData(false);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while deleting recipient.";
      setRecipientError(message);
    } finally {
      setIsDeletingRecipient(null);
    }
  };

  const handleUpdateRecipientPermission = async (email: string, canActivate: boolean) => {
    setRecipientMessage(null);
    setRecipientError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setRecipientError("API URL or vault identifier is missing.");
      return;
    }

    setIsUpdatingRecipientPermission(email);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/recipients/${encodeURIComponent(email)}`,
        {
          method: "PATCH",
          headers: buildAuthHeaders(authToken, true),
          body: JSON.stringify({ can_activate: canActivate }),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to update recipient permission.");
        throw new Error(message);
      }

      setRecipientMessage("Recipient permission updated.");
      await fetchVaultData(false);
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Unexpected error while updating recipient permission.";
      setRecipientError(message);
    } finally {
      setIsUpdatingRecipientPermission(null);
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
      formData.append("recipient_emails_json", JSON.stringify(selectedRecipientEmails));

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
      setSelectedRecipientEmails([]);
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

  const handleSelectedFileChange = (file: File | null) => {
    setSelectedFile(file);
    if (!file) {
      setSelectedRecipientEmails([]);
      return;
    }

    setSelectedRecipientEmails((vault?.recipients || []).map((recipient) => recipient.email));
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
        const message = await getErrorDetail(response, "Failed to download file.");
        throw new Error(message);
      }

      const matchingFile = files.find((fileItem) => fileItem.id === fileId);
      const blob = await response.blob();
      triggerBrowserDownload(
        blob,
        getDownloadFileName(response, matchingFile?.file_name || `${fileId}.bin`),
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while downloading file.";
      setDownloadError(message);
    } finally {
      setDownloadingFileId(null);
    }
  };

  const handleDownloadDeliveryPackage = async (recipientEmail?: string) => {
    setDownloadError(null);
    if (!apiUrl || !vaultId || !authToken) {
      setDownloadError("API URL or vault identifier is missing.");
      return;
    }

    setIsDownloadingPackage(true);
    setDownloadingPackageRecipient(recipientEmail || null);
    try {
      const searchParams = new URLSearchParams();
      if (recipientEmail) {
        searchParams.set("recipient_email", recipientEmail);
      }
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/delivery-package${searchParams.size ? `?${searchParams.toString()}` : ""}`,
        {
          headers: buildAuthHeaders(authToken, false),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to download delivery package.");
        throw new Error(message);
      }

      const blob = await response.blob();
      triggerBrowserDownload(
        blob,
        getDownloadFileName(response, vault?.delivery_file_name || buildDeliveryZipFallbackName(vault)),
      );
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Unexpected error while downloading the delivery package.";
      setDownloadError(message);
    } finally {
      setIsDownloadingPackage(false);
      setDownloadingPackageRecipient(null);
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
      setUpdateError(`Grace period must be at least 1 ${editableGracePeriodUnit === "hours" ? "hour" : "day"}.`);
      return;
    }

    if (!Number.isFinite(editableThreshold) || editableThreshold < 1) {
      setUpdateError("Activation threshold must be at least 1.");
      return;
    }
    if (activatableRecipientsCount > 0 && editableThreshold > activatableRecipientsCount) {
      setUpdateError("Activation threshold cannot exceed the number of recipients allowed to activate.");
      return;
    }

    setIsUpdatingVault(true);
    try {
      const response = await fetch(`${apiUrl}/vaults/${encodeURIComponent(vaultId)}`, {
        method: "PATCH",
        headers: buildAuthHeaders(authToken, true),
        body: JSON.stringify({
          name: normalizedName,
          owner_message: editableOwnerMessage.trim() || null,
          grace_period_value: Number(editableGracePeriod),
          grace_period_unit: editableGracePeriodUnit,
          activation_threshold:
            activatableRecipientsCount > 0
              ? Math.min(Math.floor(Number(editableThreshold)), activatableRecipientsCount)
              : Math.floor(Number(editableThreshold)),
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

  const handleCheckIn = async () => {
    setCheckInMessage(null);
    setCheckInError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setCheckInError("API URL or vault identifier is missing.");
      return;
    }

    setIsCheckingIn(true);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/check-in`,
        {
          method: "POST",
          headers: buildAuthHeaders(authToken, true),
        }
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to check in.");
        throw new Error(message);
      }

      setCheckInMessage("Check-in recorded. Activation requests were cleared.");
      await fetchVaultData(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected check-in error.";
      setCheckInError(message);
    } finally {
      setIsCheckingIn(false);
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

  const activationRequests = vault?.activation_requests ?? [];
  const activationThreshold = vault?.activation_threshold ?? 1;
  const activationCount = activationRequests.length;
  const activatableRecipients = (vault?.recipients ?? []).filter((recipient) => recipient.can_activate);
  const activatableRecipientsCount = activatableRecipients.length;
  const thresholdInputMax = activatableRecipientsCount > 0 ? activatableRecipientsCount : 1;
  const normalizedStatus = (vault?.status || "active").toLowerCase();
  const isArchivedFinal =
    normalizedStatus === "delivered" || normalizedStatus === "delivered_archived";
  const isPendingOrGrace =
    normalizedStatus === "pending_activation" || normalizedStatus === "grace_period";

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
                  Vault Ref: {vaultId || "Unavailable"}
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
              <ThemeToggleButton />
              <ButtonLink href="/profile" variant="Primary">
                Profile
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
                <Badge
                  label={formatStatusLabel(vault.status)}
                  variant={statusBadgeVariant(vault.status)}
                  size="sm"
                />
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Grace Period</Text>
                <Text variant="label">{formatGracePeriod(vault.grace_period_value, vault.grace_period_unit)}</Text>
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Recipients</Text>
                <Text variant="label">{vault.recipients.length}</Text>
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Activation Votes</Text>
                <Text variant="label">
                  {activationCount}/{activationThreshold}
                </Text>
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Delivery Package</Text>
                <Text variant="label">
                  {(vault.delivery_packages?.length || vault.delivery_blob_name)
                    ? "Ready"
                    : normalizedStatus === "delivery_initiated"
                      ? "Building"
                      : "Not ready"}
                </Text>
              </Card>
            </div>
          ) : null}

          {vault && isArchivedFinal ? (
            <Alert
              variant="info"
              message="This vault has already been delivered and archived. Vault settings, recipients, and files are now locked."
            />
          ) : null}

          {vault && isPendingOrGrace ? (
            <Card
              variant="secondary"
              style={{
                padding: t.space.m,
                gap: t.space.xs,
                border: `1px solid ${t.colors.status.warning ?? "rgba(255,165,0,0.4)"}`,
              }}
            >
              <Text variant="label" weight="semibold">
                {normalizedStatus === "pending_activation"
                  ? "Recipients are requesting activation"
                  : "Grace period in progress"}
              </Text>

              {normalizedStatus === "pending_activation" ? (
                <Text variant="bodySmall" color="secondary">
                  {activationCount} of {activationThreshold} required recipients have requested
                  activation. Use Check-In below to confirm you are still active and reset the
                  request counters.
                </Text>
              ) : (
                <>
                  <Text variant="bodySmall" color="secondary">
                    Threshold reached. Grace period started at {formatIsoDate(vault.grace_period_started_at)}.
                  </Text>
                  <Text variant="bodySmall" color="secondary">
                    Grace period ends at {formatIsoDate(vault.grace_period_expires_at)} ({describeCountdown(vault.grace_period_expires_at)}).
                  </Text>
                  <Text variant="bodySmall" color="secondary">
                    Check in before it expires to cancel delivery.
                  </Text>
                </>
              )}

              <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap", marginTop: t.space.xs }}>
                <Button
                  type="button"
                  variant="SolidPrimary"
                  onClick={() => void handleCheckIn()}
                  disabled={isCheckingIn}
                >
                  {isCheckingIn ? "Checking in..." : "I'm still here - Check In"}
                </Button>
              </div>

              {checkInError ? <Alert variant="error" message={checkInError} /> : null}
              {checkInMessage ? <Alert variant="success" message={checkInMessage} /> : null}
            </Card>
          ) : null}

          {vault && !isPendingOrGrace && vault.last_check_in_at ? (
            <Text variant="caption" color="muted">
              Last check-in: {formatIsoDate(vault.last_check_in_at)}
            </Text>
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
                    disabled={isArchivedFinal}
                    required
                  />

                  <label
                    htmlFor="vault-grace-unit"
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: t.space.xs,
                    }}
                  >
                    <Text variant="label" color="secondary">Grace Period Unit</Text>
                    <select
                      id="vault-grace-unit"
                      value={editableGracePeriodUnit}
                      onChange={(event) => setEditableGracePeriodUnit(event.target.value as "days" | "hours")}
                      disabled={isArchivedFinal}
                      style={{
                        width: "100%",
                        border: `1px solid ${t.colors.components.input.border}`,
                        backgroundColor: t.colors.components.input.bg,
                        color: t.colors.text.primary,
                        borderRadius: t.radius.full,
                        padding: `${t.space.s}px ${t.space.m}px`,
                        fontFamily: "var(--font-geist-sans), sans-serif",
                        fontSize: t.typography.body.fontSize,
                      }}
                    >
                      <option value="days">Days</option>
                      <option value="hours">Hours</option>
                    </select>
                  </label>

                  <Input
                    id="vault-grace"
                    type="number"
                    min={1}
                    max={editableGracePeriodUnit === "days" ? 3650 : 87600}
                    label={`Grace Period (${editableGracePeriodUnit})`}
                    value={editableGracePeriod}
                    onChange={(event) => setEditableGracePeriod(Number(event.target.value))}
                    disabled={isArchivedFinal}
                    required
                  />

                  <Input
                    id="vault-owner-message"
                    label="Message For Recipients"
                    value={editableOwnerMessage}
                    onChange={(event) => setEditableOwnerMessage(event.target.value)}
                    placeholder="This message appears on the generated cover PDF."
                    maxLength={4000}
                    disabled={isArchivedFinal}
                    multiline
                  />

                  <Input
                    id="vault-threshold"
                    type="number"
                    min={1}
                    max={thresholdInputMax}
                    label="Activation Threshold (recipient votes)"
                    value={editableThreshold}
                    onChange={(event) => setEditableThreshold(Number(event.target.value))}
                    disabled={isArchivedFinal}
                    required
                  />

                  <Text variant="bodySmall" color="secondary">
                    {activatableRecipientsCount > 0
                      ? `Only ${activatableRecipientsCount} recipient${activatableRecipientsCount === 1 ? "" : "s"} can request activation right now, so the threshold cannot be higher than that.`
                      : "No recipient is currently allowed to request activation."}
                  </Text>

                  <Text variant="bodySmall" color="secondary">
                    Vault status is managed automatically by activation requests, grace periods, and the delivery pipeline.
                  </Text>

                  <Button
                    type="submit"
                    size="full"
                    variant="SolidPrimary"
                    disabled={isUpdatingVault || isArchivedFinal}
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
                <Text variant="h3">Delivery Package</Text>
                <Text variant="bodySmall" color="secondary">
                  When the grace period expires, the worker decrypts the vault files, generates a
                  cover PDF for each recipient, and publishes separate ZIP packages for delivery.
                </Text>

                {vault?.delivery_error ? (
                  <Alert variant="error" message={`Last delivery error: ${vault.delivery_error}`} />
                ) : null}

                {vault?.delivery_packages?.length ? (
                  <>
                    <Alert
                      variant="success"
                      message={
                        vault.delivered_at
                          ? `Delivery packages ready since ${formatIsoDate(vault.delivered_at)}.`
                          : "Delivery packages ready."
                      }
                    />
                    <div style={{ display: "grid", gap: t.space.xs }}>
                      {vault.delivery_packages.map((deliveryPackage) => (
                        <Card
                          key={deliveryPackage.recipient_email}
                          variant="secondary"
                          style={{ padding: t.space.s, gap: t.space.xs }}
                        >
                          <Text variant="label">{deliveryPackage.recipient_email}</Text>
                          <Text variant="caption" color="muted">
                            {deliveryPackage.delivered_at
                              ? `Ready since ${formatIsoDate(deliveryPackage.delivered_at)}`
                              : "Ready"}
                          </Text>
                          <Button
                            type="button"
                            variant="SolidPrimary"
                            onClick={() => void handleDownloadDeliveryPackage(deliveryPackage.recipient_email)}
                            disabled={
                              isDownloadingPackage &&
                              downloadingPackageRecipient === deliveryPackage.recipient_email
                            }
                          >
                            {isDownloadingPackage &&
                            downloadingPackageRecipient === deliveryPackage.recipient_email
                              ? "Downloading..."
                              : "Download Delivery ZIP"}
                          </Button>
                        </Card>
                      ))}
                    </div>
                  </>
                ) : vault?.delivery_blob_name ? (
                  <Button
                    type="button"
                    variant="SolidPrimary"
                    onClick={() => void handleDownloadDeliveryPackage()}
                    disabled={isDownloadingPackage}
                  >
                    {isDownloadingPackage ? "Downloading..." : "Download Delivery ZIP"}
                  </Button>
                ) : normalizedStatus === "delivery_initiated" ? (
                  <Alert
                    variant="info"
                    message="The delivery job has started. Refresh this page in a moment to check whether the final ZIP is ready."
                  />
                ) : (
                  <Alert
                    variant="info"
                    message="No final delivery package exists yet. It only appears after the grace period expires and the delivery job completes."
                  />
                )}
              </Card>

              <Card variant="elevated" style={{ gap: t.space.s }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: t.space.xs,
                  }}
                >
                  <Text variant="h3">Activation Requests</Text>
                  <Badge
                    label={`${activationCount}/${activationThreshold}`}
                    variant={
                      activationCount >= activationThreshold
                        ? "warning"
                        : activationCount > 0
                          ? "warning"
                          : "default"
                    }
                    size="sm"
                    outlineOnly
                  />
                </div>
                <Text variant="bodySmall" color="secondary">
                  Recipients who have asked to start the delivery process.
                </Text>

                {activationRequests.length === 0 ? (
                  <Alert variant="info" message="No activation requests yet." />
                ) : (
                  <div style={{ display: "grid", gap: t.space.xs }}>
                    {activationRequests.map((request) => (
                      <Card
                        key={`${request.recipient_email}-${request.requested_at}`}
                        variant="secondary"
                        style={{ padding: t.space.s, gap: t.space.xxs }}
                      >
                        <Text variant="label">{request.recipient_email}</Text>
                        <Text variant="caption" color="muted">
                          Requested at {formatIsoDate(request.requested_at)}
                        </Text>
                        {request.reason ? (
                          <Text variant="bodySmall" color="secondary">
                            Reason: {request.reason}
                          </Text>
                        ) : null}
                      </Card>
                    ))}
                  </div>
                )}
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
                    disabled={isArchivedFinal}
                    required
                  />
                  <label
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: t.space.xs,
                      color: t.colors.text.secondary,
                      fontSize: t.typography.bodySmall.fontSize,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={newRecipientCanActivate}
                      onChange={(event) => setNewRecipientCanActivate(event.target.checked)}
                      disabled={isArchivedFinal}
                    />
                    Can request activation
                  </label>
                  <Button
                    type="submit"
                    size="full"
                    variant="SolidPrimary"
                    disabled={isAddingRecipient || isArchivedFinal}
                  >
                    {isAddingRecipient ? "Adding Recipient..." : "Add Recipient"}
                  </Button>
                </form>

                {recipientError ? <Alert variant="error" message={recipientError} /> : null}
                {recipientMessage ? <Alert variant="success" message={recipientMessage} /> : null}

                <div style={{ display: "grid", gap: t.space.xs }}>
                  {vault?.recipients.length ? (
                    vault.recipients.map((recipient) => (
                      <Card key={recipient.email} variant="secondary" style={{ padding: t.space.s, gap: t.space.xs }}>
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "flex-start",
                            gap: t.space.xs,
                            flexWrap: "wrap",
                          }}
                        >
                          <div style={{ display: "grid", gap: t.space.xxs }}>
                            <Text variant="bodySmall">{recipient.email}</Text>
                            <label
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: t.space.xs,
                                color: t.colors.text.secondary,
                                fontSize: t.typography.bodySmall.fontSize,
                              }}
                            >
                              <input
                                type="checkbox"
                                checked={recipient.can_activate}
                                disabled={isArchivedFinal || isUpdatingRecipientPermission === recipient.email}
                                onChange={(event) =>
                                  void handleUpdateRecipientPermission(
                                    recipient.email,
                                    event.target.checked,
                                  )
                                }
                              />
                              Can request activation
                            </label>
                          </div>
                          <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
                            <Badge
                              label={recipient.can_activate ? "Can activate" : "Cannot activate"}
                              variant={recipient.can_activate ? "success" : "default"}
                              size="sm"
                              outlineOnly
                            />
                            <Button
                              type="button"
                              size="default"
                              variant="Destructive"
                              disabled={isDeletingRecipient === recipient.email || isArchivedFinal}
                              onClick={() => void handleDeleteRecipient(recipient.email)}
                            >
                              {isDeletingRecipient === recipient.email ? "Removing..." : "Remove"}
                            </Button>
                          </div>
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
                    onChange={(event) => handleSelectedFileChange(event.target.files?.[0] ?? null)}
                    disabled={isArchivedFinal}
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
                  {selectedFile ? (
                    <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.s }}>
                      <Text variant="label">Recipients for this file</Text>
                      {vault?.recipients.length ? (
                        <div style={{ display: "grid", gap: t.space.xs }}>
                          {vault.recipients.map((recipient) => (
                            <label
                              key={recipient.email}
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: t.space.xs,
                                color: t.colors.text.secondary,
                                fontSize: t.typography.bodySmall.fontSize,
                              }}
                            >
                              <input
                                type="checkbox"
                                checked={selectedRecipientEmails.includes(recipient.email)}
                                onChange={(event) =>
                                  setSelectedRecipientEmails((currentSelection) =>
                                    event.target.checked
                                      ? [...currentSelection, recipient.email].filter(
                                          (value, index, values) => values.indexOf(value) === index,
                                        )
                                      : currentSelection.filter((value) => value !== recipient.email),
                                  )
                                }
                                disabled={isArchivedFinal}
                              />
                              {recipient.email}
                            </label>
                          ))}
                        </div>
                      ) : (
                        <Alert
                          variant="info"
                          message="This vault has no recipients yet, so the uploaded file will not be assigned to anyone."
                        />
                      )}
                    </Card>
                  ) : null}
                  <Button
                    type="submit"
                    size="full"
                    variant="SolidPrimary"
                    disabled={isUploadingFile || isArchivedFinal}
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
                          disabled={isDeletingFileId === fileItem.id || isArchivedFinal}
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
                          Stored: {formatBytes(fileItem.ciphertext_size_bytes)}
                        </Text>
                        <Text variant="caption" color="secondary">
                          Type: {fileItem.content_type || "Unknown"}
                        </Text>
                        <Text variant="caption" color="secondary">
                          Uploaded: {fileItem.uploaded_at || "Unknown"}
                        </Text>
                        <Text variant="caption" color="secondary">
                          Encryption: {fileItem.encrypted ? fileItem.algorithm || "Encrypted" : "Legacy plaintext"}
                        </Text>
                        <Text variant="caption" color="secondary">
                          Recipients: {fileItem.recipient_emails?.length ? fileItem.recipient_emails.join(", ") : "No recipients assigned"}
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
