"use client";

import { useEffect } from "react";

import { Button, Card, Text, useCatTheme } from "@/components/catmagui";

type DocumentProtectionDialogProps = {
  isOpen: boolean;
  onClose: () => void;
};

const SECURITY_POINTS = [
  {
    title: "Files encrypt in your browser before upload",
    body:
      "Each vault file is encrypted in the browser with AES-256-GCM before upload. The server stores ciphertext only and does not receive the recovery key needed to read the document.",
  },
  {
    title: "Recovery keys stay with the user",
    body:
      "Each vault has a recovery key that you must save. We keep only a verifier, not the readable key itself, so losing it means the encrypted files cannot be recovered by the platform.",
  },
  {
    title: "Recipients get encrypted delivery bundles",
    body:
      "When a vault is delivered, recipients receive encrypted files and metadata for decryption in the client. The delivery worker can package the files without opening their contents.",
  },
];

export default function DocumentProtectionDialog({
  isOpen,
  onClose,
}: DocumentProtectionDialogProps) {
  const t = useCatTheme();

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) {
    return null;
  }

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: t.space.m,
        backgroundColor: t.isDark ? "rgba(5, 5, 5, 0.78)" : "rgba(9, 9, 11, 0.42)",
        backdropFilter: "blur(10px)",
      }}
    >
      <Card
        variant="elevated"
        style={{
          width: "min(760px, 100%)",
          maxHeight: "calc(100vh - 32px)",
          overflowY: "auto",
          gap: t.space.m,
          padding: t.space.xl,
          boxShadow: t.isDark
            ? "0 28px 80px rgba(0, 0, 0, 0.55)"
            : "0 28px 80px rgba(15, 23, 42, 0.18)",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="document-protection-title"
          style={{ display: "grid", gap: t.space.m }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-start",
              gap: t.space.m,
              flexWrap: "wrap",
            }}
          >
            <div style={{ display: "grid", gap: t.space.xs, flex: 1, minWidth: 260 }}>
              <Text variant="caption" color="brand" weight="semibold">
                Security Architecture
              </Text>
              <Text variant="h2" style={{ maxWidth: 560 }} id="document-protection-title">
                How we protect your documents
              </Text>
              <Text variant="bodySmall" color="secondary" style={{ maxWidth: 620 }}>
                Last Writes uses zero-knowledge document protection: your browser encrypts
                files before upload and the recovery key stays with you. Direct access to
                storage or the API is not enough to read those documents.
              </Text>
            </div>

            <Button type="button" variant="Primary" onClick={onClose}>
              Close
            </Button>
          </div>

          <div style={{ display: "grid", gap: t.space.s }}>
            {SECURITY_POINTS.map((item) => (
              <Card
                key={item.title}
                variant="secondary"
                style={{
                  padding: t.space.m,
                  gap: t.space.xs,
                  borderLeft: `4px solid ${t.colors.brand}`,
                }}
              >
                <Text variant="label" weight="semibold">
                  {item.title}
                </Text>
                <Text variant="bodySmall" color="secondary">
                  {item.body}
                </Text>
              </Card>
            ))}
          </div>

          <Card
            variant="outline"
            style={{
              padding: t.space.m,
              backgroundColor: t.isDark ? "rgba(39, 39, 42, 0.24)" : "rgba(244, 244, 245, 0.72)",
            }}
          >
            <Text variant="bodySmall" color="secondary">
              Important: this guarantee applies to vault file contents. Visible vault
              metadata and recipient emails are still service metadata and are not hidden by
              file-level zero-knowledge encryption.
            </Text>
          </Card>
        </div>
      </Card>
    </div>
  );
}
