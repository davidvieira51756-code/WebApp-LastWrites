"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card, Input, Text, useCatTheme } from "@/components/catmagui";
import { downloadRecoveryKeyBackup } from "@/lib/zeroKnowledge";

type RecoveryKeyBackupPanelProps = {
  recoveryKey: string;
  vaultId: string;
  vaultName: string;
  title: string;
  description: string;
  warningMessage: string;
  confirmLabel: string;
  confirmButtonLabel: string;
  onConfirmed: () => void;
};

export default function RecoveryKeyBackupPanel({
  recoveryKey,
  vaultId,
  vaultName,
  title,
  description,
  warningMessage,
  confirmLabel,
  confirmButtonLabel,
  onConfirmed,
}: RecoveryKeyBackupPanelProps) {
  const t = useCatTheme();
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [hasConfirmedBackup, setHasConfirmedBackup] = useState(false);

  useEffect(() => {
    setStatusMessage(null);
    setStatusError(null);
    setHasConfirmedBackup(false);
  }, [recoveryKey, vaultId]);

  const handleCopy = async () => {
    setStatusMessage(null);
    setStatusError(null);

    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard access is not available in this browser.");
      }
      await navigator.clipboard.writeText(recoveryKey);
      setStatusMessage("Recovery key copied to clipboard.");
    } catch (error) {
      setStatusError(
        error instanceof Error ? error.message : "Unable to copy the recovery key.",
      );
    }
  };

  const handleDownload = () => {
    setStatusMessage(null);
    setStatusError(null);

    try {
      downloadRecoveryKeyBackup({
        recoveryKey,
        vaultId,
        vaultName,
      });
      setStatusMessage("Recovery key backup file downloaded.");
    } catch (error) {
      setStatusError(
        error instanceof Error ? error.message : "Unable to export the recovery key backup.",
      );
    }
  };

  return (
    <Card variant="outline" style={{ gap: t.space.s }}>
      <Text variant="h3">{title}</Text>
      <Text variant="bodySmall" color="secondary">
        {description}
      </Text>

      <Alert variant="error" message={warningMessage} />

      <Input
        id={`recovery-key-${vaultId}`}
        label="Recovery Key"
        value={recoveryKey}
        readOnly
      />

      <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
        <Button type="button" variant="Primary" onClick={() => void handleCopy()}>
          Copy Recovery Key
        </Button>
        <Button type="button" variant="Secondary" onClick={handleDownload}>
          Download Backup File
        </Button>
      </div>

      <label
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: t.space.xs,
          color: t.colors.text.secondary,
          fontSize: t.typography.bodySmall.fontSize,
        }}
      >
        <input
          type="checkbox"
          checked={hasConfirmedBackup}
          onChange={(event) => setHasConfirmedBackup(event.target.checked)}
        />
        <span>{confirmLabel}</span>
      </label>

      <Button
        type="button"
        size="full"
        variant="SolidPrimary"
        disabled={!hasConfirmedBackup}
        onClick={onConfirmed}
      >
        {confirmButtonLabel}
      </Button>

      {statusError ? <Alert variant="error" message={statusError} /> : null}
      {statusMessage ? <Alert variant="success" message={statusMessage} /> : null}
    </Card>
  );
}
