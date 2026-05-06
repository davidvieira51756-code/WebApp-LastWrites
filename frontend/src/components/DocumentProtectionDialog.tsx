"use client";

import { useEffect } from "react";

import { Button, Card, Text, useCatTheme } from "@/components/catmagui";

type DocumentProtectionDialogProps = {
  isOpen: boolean;
  onClose: () => void;
};

const SECURITY_POINTS = [
  {
    title: "Encrypted before storage",
    body:
      "Every uploaded file is encrypted with AES-256-GCM before it is written to storage, so the blob store does not keep a readable plaintext copy of your document.",
  },
  {
    title: "Keys are separated from files",
    body:
      "Each vault uses its own RSA key pair. The one-time file key is wrapped with RSA-OAEP-256, while the private key stays in the vault key service instead of beside the file itself.",
  },
  {
    title: "Integrity is checked",
    body:
      "We keep SHA-256 integrity metadata and verify the decrypted file before serving it back, which helps detect tampering or corruption.",
  },
  {
    title: "Access stays controlled",
    body:
      "Plaintext is only produced inside authenticated workflows such as an owner download or a final delivery package for an entitled recipient. This protects files at rest and limits exposure, but it is not a zero-knowledge design.",
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
                Last Writes encrypts uploaded files before storage and keeps the file
                encryption key separate from the ciphertext. That means direct storage
                access alone is not enough to read the document.
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
              Important: this is strong server-side protection, not a claim that the
              application can never decrypt a file. The backend must decrypt files to
              deliver them to the owner or to assemble the final recipient package.
            </Text>
          </Card>
        </div>
      </Card>
    </div>
  );
}
