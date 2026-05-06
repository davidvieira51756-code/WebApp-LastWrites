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
import {
  clearStoredRecoveryKeyForVault,
  decryptVaultFile,
  getStoredRecoveryKeyForVault,
  storeRecoveryKeyForVault,
  verifyRecoveryKey,
} from "@/lib/zeroKnowledge";

type RecipientVaultSummary = {
  id: string;
  name: string;
  owner_display_name?: string | null;
  status: string;
  grace_period_days: number;
  activation_threshold: number;
  activation_requests_count: number;
  has_requested_activation: boolean;
  can_activate: boolean;
  grace_period_expires_at?: string | null;
  delivered_at?: string | null;
  delivery_available?: boolean;
  recovery_key_verifier?: string | null;
};

type DeliveryFile = {
  id: string;
  file_name: string;
  content_type?: string | null;
  zero_knowledge?: boolean;
  kdf_salt?: string | null;
  iv?: string | null;
};

type DeliveryFilesResponse = {
  vault_id: string;
  zero_knowledge_enabled: boolean;
  recovery_key_verifier?: string | null;
  files: DeliveryFile[];
};

function normalizeVaultId(rawVaultId: string | string[] | undefined): string {
  if (Array.isArray(rawVaultId)) {
    return rawVaultId[0] ?? "";
  }
  return rawVaultId ?? "";
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

function buildDeliveryZipFallbackName(summary?: RecipientVaultSummary | null): string {
  if (!summary) {
    return "vault-delivery.zip";
  }

  return `${summary.name}-${summary.id}.zip`;
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

export default function RecipientActivationPage() {
  const t = useCatTheme();
  const router = useRouter();
  const params = useParams<{ vault_id?: string | string[] }>();
  const vaultId = useMemo(() => normalizeVaultId(params?.vault_id), [params]);
  const apiUrl = useMemo(() => getApiUrl(), []);

  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [signedInEmail, setSignedInEmail] = useState<string | null>(null);

  const [summary, setSummary] = useState<RecipientVaultSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);

  const [reason, setReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isWithdrawing, setIsWithdrawing] = useState(false);
  const [isDownloadingPackage, setIsDownloadingPackage] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deliveryFiles, setDeliveryFiles] = useState<DeliveryFile[]>([]);
  const [isLoadingDeliveryFiles, setIsLoadingDeliveryFiles] = useState(false);
  const [recoveryKey, setRecoveryKey] = useState<string | null>(null);
  const [recoveryKeyInput, setRecoveryKeyInput] = useState("");
  const [isDownloadingFileId, setIsDownloadingFileId] = useState<string | null>(null);

  const authRedirectPath = useMemo(() => {
    if (!vaultId) {
      return "/";
    }
    return `/incoming/${encodeURIComponent(vaultId)}`;
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

  const fetchSummary = useCallback(async () => {
    if (!apiUrl) {
      setPageError("NEXT_PUBLIC_API_URL is not configured.");
      setIsLoading(false);
      return;
    }
    if (!vaultId) {
      setPageError("Vault identifier is missing in the URL.");
      setIsLoading(false);
      return;
    }
    if (!authToken) {
      return;
    }

    setIsLoading(true);
    setPageError(null);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/activation-summary`,
        {
          method: "GET",
          headers: buildAuthHeaders(authToken, false),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to load activation details.");
        throw new Error(message);
      }

      const payload = (await response.json()) as RecipientVaultSummary;
      setSummary(payload);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while loading vault.";
      setPageError(message);
    } finally {
      setIsLoading(false);
    }
  }, [apiUrl, authToken, handleUnauthorized, vaultId]);

  useEffect(() => {
    if (!isCheckingAuth && authToken) {
      void fetchSummary();
    }
  }, [authToken, fetchSummary, isCheckingAuth]);

  const fetchDeliveryFiles = useCallback(async () => {
    if (!apiUrl || !vaultId || !authToken || !summary?.delivery_available) {
      return;
    }

    setIsLoadingDeliveryFiles(true);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/delivery-files`,
        {
          headers: buildAuthHeaders(authToken, false),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }
      if (!response.ok) {
        throw new Error(await getErrorDetail(response, "Failed to load delivery files."));
      }

      const payload = (await response.json()) as DeliveryFilesResponse;
      setDeliveryFiles(Array.isArray(payload.files) ? payload.files : []);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Unexpected delivery file load error.");
      setDeliveryFiles([]);
    } finally {
      setIsLoadingDeliveryFiles(false);
    }
  }, [apiUrl, authToken, handleUnauthorized, summary?.delivery_available, vaultId]);

  useEffect(() => {
    if (summary?.delivery_available) {
      void fetchDeliveryFiles();
    } else {
      setDeliveryFiles([]);
    }
  }, [fetchDeliveryFiles, summary?.delivery_available]);

  useEffect(() => {
    if (!vaultId || !summary?.recovery_key_verifier) {
      setRecoveryKey(null);
      return;
    }
    const storedRecoveryKey = getStoredRecoveryKeyForVault(vaultId);
    if (!storedRecoveryKey) {
      setRecoveryKey(null);
      return;
    }

    let isCancelled = false;
    void (async () => {
      const isValid = await verifyRecoveryKey(
        storedRecoveryKey,
        vaultId,
        summary.recovery_key_verifier,
      );
      if (isCancelled) {
        return;
      }
      if (isValid) {
        setRecoveryKey(storedRecoveryKey);
      } else {
        clearStoredRecoveryKeyForVault(vaultId);
        setRecoveryKey(null);
      }
    })();

    return () => {
      isCancelled = true;
    };
  }, [summary?.recovery_key_verifier, vaultId]);

  const handleSignOut = useCallback(() => {
    clearAuthSession();
    setAuthToken(null);
    router.replace("/auth");
  }, [router]);

  const handleSubmitRequest = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setActionMessage(null);
    setActionError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setActionError("API URL or vault identifier is missing.");
      return;
    }

    setIsSubmitting(true);
    try {
      const trimmedReason = reason.trim();
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/activation-requests`,
        {
          method: "POST",
          headers: buildAuthHeaders(authToken, true),
          body: JSON.stringify({ reason: trimmedReason ? trimmedReason : null }),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to submit activation request.");
        throw new Error(message);
      }

      const payload = (await response.json()) as RecipientVaultSummary;
      setSummary(payload);
      setReason("");
      setActionMessage("Activation request submitted.");
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while submitting the request.";
      setActionError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleWithdrawRequest = async () => {
    setActionMessage(null);
    setActionError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setActionError("API URL or vault identifier is missing.");
      return;
    }

    setIsWithdrawing(true);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/activation-requests`,
        {
          method: "DELETE",
          headers: buildAuthHeaders(authToken, false),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to withdraw activation request.");
        throw new Error(message);
      }

      const payload = (await response.json()) as RecipientVaultSummary;
      setSummary(payload);
      setActionMessage("Activation request withdrawn.");
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while withdrawing the request.";
      setActionError(message);
    } finally {
      setIsWithdrawing(false);
    }
  };

  const handleUnlockRecoveryKey = async () => {
    setActionMessage(null);
    setActionError(null);

    if (!vaultId || !summary?.recovery_key_verifier) {
      setActionError("This delivery does not expose a recovery verifier.");
      return;
    }

    const isValid = await verifyRecoveryKey(
      recoveryKeyInput,
      vaultId,
      summary.recovery_key_verifier,
    );
    if (!isValid) {
      setActionError("Recovery key is incorrect.");
      return;
    }

    storeRecoveryKeyForVault(vaultId, recoveryKeyInput);
    setRecoveryKey(getStoredRecoveryKeyForVault(vaultId));
    setRecoveryKeyInput("");
    setActionMessage("Recovery key unlocked on this device for the current session.");
  };

  const handleForgetRecoveryKey = () => {
    if (!vaultId) {
      return;
    }
    clearStoredRecoveryKeyForVault(vaultId);
    setRecoveryKey(null);
    setRecoveryKeyInput("");
    setActionMessage("Recovery key removed from this device session.");
    setActionError(null);
  };

  const handleDownloadPackage = async () => {
    setActionMessage(null);
    setActionError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setActionError("API URL or vault identifier is missing.");
      return;
    }

    setIsDownloadingPackage(true);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/delivery-package`,
        {
          method: "GET",
          headers: buildAuthHeaders(authToken, false),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }

      if (!response.ok) {
        const message = await getErrorDetail(response, "Failed to download the delivery package.");
        throw new Error(message);
      }

      const blob = await response.blob();
      triggerBrowserDownload(
        blob,
        getDownloadFileName(response, buildDeliveryZipFallbackName(summary)),
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error while downloading the package.";
      setActionError(message);
    } finally {
      setIsDownloadingPackage(false);
    }
  };

  const handleDownloadDeliveryFile = async (fileItem: DeliveryFile) => {
    setActionMessage(null);
    setActionError(null);

    if (!apiUrl || !vaultId || !authToken) {
      setActionError("API URL or vault identifier is missing.");
      return;
    }

    setIsDownloadingFileId(fileItem.id);
    try {
      const response = await fetch(
        `${apiUrl}/vaults/${encodeURIComponent(vaultId)}/files/${encodeURIComponent(fileItem.id)}/download`,
        {
          headers: buildAuthHeaders(authToken, false),
        },
      );

      if (isUnauthorizedStatus(response.status)) {
        handleUnauthorized();
        return;
      }
      if (!response.ok) {
        throw new Error(await getErrorDetail(response, "Failed to download delivery file."));
      }

      if (fileItem.zero_knowledge) {
        if (!recoveryKey || !fileItem.kdf_salt || !fileItem.iv) {
          throw new Error("Unlock the delivery with the recovery key before downloading files.");
        }
        const ciphertext = await response.arrayBuffer();
        const plaintext = await decryptVaultFile(ciphertext, {
          recoveryKey,
          vaultId,
          kdfSalt: fileItem.kdf_salt,
          iv: fileItem.iv,
        });
        const plaintextBuffer = plaintext.buffer.slice(
          plaintext.byteOffset,
          plaintext.byteOffset + plaintext.byteLength,
        ) as ArrayBuffer;
        triggerBrowserDownload(
          new Blob([plaintextBuffer], {
            type: fileItem.content_type || "application/octet-stream",
          }),
          fileItem.file_name,
        );
      } else {
        const blob = await response.blob();
        triggerBrowserDownload(blob, getDownloadFileName(response, fileItem.file_name));
      }
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "Unexpected error while downloading the file.",
      );
    } finally {
      setIsDownloadingFileId(null);
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
        <div style={{ margin: "0 auto", width: "100%", maxWidth: 820 }}>
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

  const summaryStatus = summary?.status ?? "";
  const normalizedStatus = summaryStatus.toLowerCase();
  const requestsCount = summary?.activation_requests_count ?? 0;
  const threshold = summary?.activation_threshold ?? 1;
  const isTerminal =
    normalizedStatus === "delivery_initiated" ||
    normalizedStatus === "delivered" ||
    normalizedStatus === "delivered_archived" ||
    normalizedStatus === "disabled";
  const isActivationBlocked = isTerminal || !summary?.can_activate;

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
      <div style={{ margin: "0 auto", width: "100%", maxWidth: 820, display: "grid", gap: t.space.m }}>
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
              <Text variant="h2">{summary ? summary.name : "Incoming Vault"}</Text>
              {summary?.owner_display_name ? (
                <Text variant="bodySmall" color="secondary">
                  Owner: {summary.owner_display_name}
                </Text>
              ) : null}
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
              <ButtonLink href="/profile" variant="Primary">
                Profile
              </ButtonLink>
              <Button
                type="button"
                onClick={() => void fetchSummary()}
                disabled={isLoading}
                variant="SolidPrimary"
              >
                {isLoading ? "Refreshing..." : "Refresh"}
              </Button>
              <Button type="button" onClick={handleSignOut} variant="Destructive">
                Sign Out
              </Button>
            </div>
          </div>

          {summary ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                gap: t.space.xs,
              }}
            >
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Status</Text>
                <Badge
                  label={formatStatusLabel(summary.status)}
                  variant={statusBadgeVariant(summary.status)}
                  size="sm"
                />
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Votes</Text>
                <Text variant="label">
                  {requestsCount}/{threshold}
                </Text>
              </Card>
              <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                <Text variant="caption" color="muted">Grace Period</Text>
                <Text variant="label">{summary.grace_period_days} days</Text>
              </Card>
              {summary.grace_period_expires_at ? (
                <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                  <Text variant="caption" color="muted">Grace ends</Text>
                  <Text variant="label">{formatIsoDate(summary.grace_period_expires_at)}</Text>
                </Card>
              ) : null}
              {summary.delivery_available ? (
                <Card variant="secondary" style={{ padding: t.space.s, gap: t.space.xxs }}>
                  <Text variant="caption" color="muted">Delivery</Text>
                  <Text variant="label">
                    {summary.delivered_at ? `Ready since ${formatIsoDate(summary.delivered_at)}` : "Ready"}
                  </Text>
                </Card>
              ) : null}
            </div>
          ) : null}
        </Card>

        {isLoading ? <Alert variant="info" message="Loading vault..." /> : null}

        {!isLoading && pageError ? (
          <Card variant="secondary" style={{ gap: t.space.s }}>
            <Alert variant="error" message={pageError} />
            <div>
              <Button type="button" onClick={() => void fetchSummary()} variant="Primary">
                Retry
              </Button>
            </div>
          </Card>
        ) : null}

        {!isLoading && !pageError && summary ? (
          <Card variant="elevated" style={{ gap: t.space.s }}>
            <Text variant="h3">Request Activation</Text>
            <Text variant="bodySmall" color="secondary">
              By requesting activation, you are signalling that you believe the owner is no
              longer able to check in. Once the required number of recipients ({threshold})
              have requested activation, the grace period will start. If the owner does not
              check in before it ends, the vault will be delivered.
            </Text>

            {summary.delivery_available ? (
              <Card variant="secondary" style={{ padding: t.space.m, gap: t.space.xs }}>
                <Text variant="label">The delivery package is ready.</Text>
                <Text variant="bodySmall" color="secondary">
                  Your final delivery files are now available. You can still download the archival
                  ZIP package, and if this vault uses zero-knowledge encryption you can decrypt the
                  individual files here after entering the recovery key shared by the owner.
                </Text>
                {summary.recovery_key_verifier ? (
                  recoveryKey ? (
                    <>
                      <Alert
                        variant="success"
                        message="Recovery key unlocked on this device for the current session."
                      />
                      <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
                        <Button type="button" variant="Primary" onClick={handleForgetRecoveryKey}>
                          Forget Recovery Key
                        </Button>
                      </div>
                    </>
                  ) : (
                    <>
                      <Input
                        id="delivery-recovery-key"
                        type="password"
                        label="Recovery Key"
                        value={recoveryKeyInput}
                        onChange={(event) => setRecoveryKeyInput(event.target.value)}
                        placeholder="Enter the vault recovery key"
                      />
                      <Button
                        type="button"
                        variant="SolidPrimary"
                        onClick={() => void handleUnlockRecoveryKey()}
                      >
                        Unlock Delivery Files
                      </Button>
                    </>
                  )
                ) : null}

                {isLoadingDeliveryFiles ? (
                  <Alert variant="info" message="Loading delivery files..." />
                ) : null}

                {deliveryFiles.length > 0 ? (
                  <div style={{ display: "grid", gap: t.space.xs }}>
                    {deliveryFiles.map((fileItem) => (
                      <Card
                        key={fileItem.id}
                        variant="secondary"
                        style={{ padding: t.space.s, gap: t.space.xs }}
                      >
                        <Text variant="label">{fileItem.file_name}</Text>
                        <Text variant="caption" color="muted">
                          {fileItem.zero_knowledge
                            ? "Zero-knowledge file"
                            : "Encrypted file"}
                        </Text>
                        <Button
                          type="button"
                          variant="Primary"
                          onClick={() => void handleDownloadDeliveryFile(fileItem)}
                          disabled={
                            isDownloadingFileId === fileItem.id ||
                            (fileItem.zero_knowledge && !recoveryKey)
                          }
                        >
                          {isDownloadingFileId === fileItem.id ? "Preparing..." : "Download File"}
                        </Button>
                      </Card>
                    ))}
                  </div>
                ) : null}

                <Button
                  type="button"
                  variant="Primary"
                  onClick={() => void handleDownloadPackage()}
                  disabled={isDownloadingPackage}
                >
                  {isDownloadingPackage ? "Downloading..." : "Download delivery ZIP"}
                </Button>
              </Card>
            ) : null}

            {!summary.can_activate && !summary.delivery_available ? (
              <Alert
                variant="info"
                message="The owner has not allowed this recipient to request activation for this vault."
              />
            ) : null}

            {isTerminal ? (
              <Alert
                variant="info"
                message={
                  summary.delivery_available
                    ? "This vault has already been delivered."
                    : "This vault is no longer accepting activation requests."
                }
              />
            ) : null}

            {summary.has_requested_activation ? (
              <Card variant="secondary" style={{ padding: t.space.m, gap: t.space.xs }}>
                <Text variant="label">You have already requested activation.</Text>
                <Text variant="bodySmall" color="secondary">
                  You can withdraw your request if you change your mind. Withdrawing while the
                  grace period is active will reset the timer if the threshold drops below the
                  required number.
                </Text>
                <Button
                  type="button"
                  variant="Destructive"
                  onClick={() => void handleWithdrawRequest()}
                  disabled={isWithdrawing || isActivationBlocked}
                >
                  {isWithdrawing ? "Withdrawing..." : "Withdraw my request"}
                </Button>
              </Card>
            ) : (
              <form
                onSubmit={handleSubmitRequest}
                style={{ display: "flex", flexDirection: "column", gap: t.space.s }}
              >
                <Input
                  id="activation-reason"
                  label="Reason (optional)"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Why are you requesting activation?"
                  maxLength={1000}
                />
                <Button
                  type="submit"
                  size="full"
                  variant="SolidPrimary"
                  disabled={isSubmitting || isActivationBlocked}
                >
                  {isSubmitting ? "Submitting..." : "Request activation"}
                </Button>
              </form>
            )}

            {actionError ? <Alert variant="error" message={actionError} /> : null}
            {actionMessage ? <Alert variant="success" message={actionMessage} /> : null}
          </Card>
        ) : null}
      </div>
    </main>
  );
}
