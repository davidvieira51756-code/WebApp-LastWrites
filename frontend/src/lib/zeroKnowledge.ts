"use client";

const RECOVERY_KEY_SESSION_PREFIX = "lw.zk.recovery.";
const RECOVERY_KEY_BACKUP_CONFIRMED_PREFIX = "lw.zk.recovery.backed-up.";
const ZERO_KNOWLEDGE_SCHEMA_VERSION = 4;
const ZERO_KNOWLEDGE_KDF_ALGORITHM = "HKDF-SHA256";

type ZeroKnowledgeFileMetadata = {
  encrypted: true;
  zero_knowledge: true;
  algorithm: "AES-256-GCM";
  schema_version: number;
  iv: string;
  authentication_tag_appended: true;
  plaintext_size_bytes: number;
  plaintext_sha256: string;
  ciphertext_sha256: string;
  encryption_context: string;
  owner_wrapped_file_key: string;
  owner_wrap_algorithm: "AES-256-GCM";
  owner_wrap_kdf_algorithm: string;
  owner_wrap_salt: string;
  owner_wrap_iv: string;
  recipient_wrapped_keys: Array<{
    recipient_email: string;
    wrapped_file_key: string;
    algorithm: "RSA-OAEP-256";
  }>;
};

type EncryptVaultFileResult = {
  ciphertext: Uint8Array;
  metadata: ZeroKnowledgeFileMetadata;
};

export type RecipientWrappedKeyAssignment = {
  recipient_email: string;
  wrapped_file_key: string;
  algorithm: "RSA-OAEP-256";
};

function assertCrypto(): Crypto {
  if (typeof window === "undefined" || !window.crypto?.subtle) {
    throw new Error("Web Crypto is not available in this browser.");
  }
  return window.crypto;
}

function encodeText(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function toUint8Array(value: ArrayBuffer | Uint8Array): Uint8Array {
  return value instanceof Uint8Array ? value : new Uint8Array(value);
}

function toArrayBuffer(value: Uint8Array): ArrayBuffer {
  return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) as ArrayBuffer;
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const totalLength = parts.reduce((sum, part) => sum + part.length, 0);
  const merged = new Uint8Array(totalLength);
  let offset = 0;
  for (const part of parts) {
    merged.set(part, offset);
    offset += part.length;
  }
  return merged;
}

function buildRecoveryKeyBackupFileName(vaultName: string, vaultId: string): string {
  const normalizedName = vaultName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const safeName = normalizedName || "vault";
  return `${safeName}-${vaultId}-recovery-key.txt`;
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

export function normalizeRecoveryKey(value: string): string {
  return value.trim().replace(/\s+/g, "");
}

export function generateRecoveryKey(): string {
  const crypto = assertCrypto();
  return b64urlEncode(crypto.getRandomValues(new Uint8Array(32)));
}

export function b64urlEncode(raw: Uint8Array): string {
  let binary = "";
  for (const byte of raw) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export function b64urlDecode(raw: string): Uint8Array {
  const normalized = normalizeRecoveryKey(raw);
  const paddingLength = (4 - (normalized.length % 4)) % 4;
  const padded = `${normalized}${"=".repeat(paddingLength)}`;
  const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function sha256Bytes(value: Uint8Array): Promise<Uint8Array> {
  const crypto = assertCrypto();
  return new Uint8Array(await crypto.subtle.digest("SHA-256", toArrayBuffer(value)));
}

async function importRecoveryKeyMaterial(recoveryKey: string): Promise<CryptoKey> {
  const crypto = assertCrypto();
  const recoveryKeyBytes = b64urlDecode(recoveryKey);
  if (recoveryKeyBytes.length !== 32) {
    throw new Error("Recovery key is invalid.");
  }

  return crypto.subtle.importKey("raw", toArrayBuffer(recoveryKeyBytes), "HKDF", false, ["deriveKey"]);
}

async function deriveOwnerWrapKey(
  recoveryKey: string,
  {
    vaultId,
    ownerWrapSalt,
  }: {
    vaultId: string;
    ownerWrapSalt: string;
  },
): Promise<CryptoKey> {
  const crypto = assertCrypto();
  const masterKey = await importRecoveryKeyMaterial(recoveryKey);
  return crypto.subtle.deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: toArrayBuffer(b64urlDecode(ownerWrapSalt)),
      info: toArrayBuffer(encodeText(`lastwrites:owner-file-key:${vaultId}`)),
    },
    masterKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

export async function buildRecoveryKeyVerifier(
  recoveryKey: string,
  _vaultId?: string,
): Promise<string> {
  void _vaultId;
  const normalizedKey = normalizeRecoveryKey(recoveryKey);
  const keyBytes = b64urlDecode(normalizedKey);
  if (keyBytes.length !== 32) {
    throw new Error("Recovery key is invalid.");
  }
  const digest = await sha256Bytes(concatBytes(encodeText("lastwrites:recovery-key:v1:"), keyBytes));
  return b64urlEncode(digest);
}

export async function verifyRecoveryKey(
  recoveryKey: string,
  vaultId: string,
  expectedVerifier: string | null | undefined,
): Promise<boolean> {
  void vaultId;
  if (!expectedVerifier) {
    return false;
  }
  const computedVerifier = await buildRecoveryKeyVerifier(recoveryKey);
  return computedVerifier === expectedVerifier;
}

export async function encryptVaultFile(
  plaintext: ArrayBuffer | Uint8Array,
  {
    recoveryKey,
    vaultId,
    recipientPublicKeys,
  }: {
    recoveryKey: string;
    vaultId: string;
    recipientPublicKeys: Array<{ recipientEmail: string; publicJwk: JsonWebKey }>;
  },
): Promise<EncryptVaultFileResult> {
  const crypto = assertCrypto();
  const normalizedRecoveryKey = normalizeRecoveryKey(recoveryKey);
  const plaintextBytes = toUint8Array(plaintext);
  const fileIvBytes = crypto.getRandomValues(new Uint8Array(12));
  const ownerWrapSaltBytes = crypto.getRandomValues(new Uint8Array(16));
  const ownerWrapIvBytes = crypto.getRandomValues(new Uint8Array(12));
  const fileKeyBytes = crypto.getRandomValues(new Uint8Array(32));
  const fileKey = await crypto.subtle.importKey(
    "raw",
    toArrayBuffer(fileKeyBytes),
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
  const ownerWrapSalt = b64urlEncode(ownerWrapSaltBytes);
  const ownerWrapKey = await deriveOwnerWrapKey(normalizedRecoveryKey, { vaultId, ownerWrapSalt });
  const ciphertextBuffer = await crypto.subtle.encrypt(
    {
      name: "AES-GCM",
      iv: toArrayBuffer(fileIvBytes),
    },
    fileKey,
    toArrayBuffer(plaintextBytes),
  );
  const ciphertext = new Uint8Array(ciphertextBuffer);
  const ownerWrappedFileKey = new Uint8Array(
    await crypto.subtle.encrypt(
      {
        name: "AES-GCM",
        iv: toArrayBuffer(ownerWrapIvBytes),
      },
      ownerWrapKey,
      toArrayBuffer(fileKeyBytes),
    ),
  );
  const plaintextSha = await sha256Bytes(plaintextBytes);
  const ciphertextSha = await sha256Bytes(ciphertext);
  const recipientWrappedKeys: ZeroKnowledgeFileMetadata["recipient_wrapped_keys"] = [];

  for (const recipientPublicKey of recipientPublicKeys) {
    const recipientEmail = recipientPublicKey.recipientEmail.trim().toLowerCase();
    if (!recipientEmail) {
      continue;
    }
    const importedPublicKey = await crypto.subtle.importKey(
      "jwk",
      recipientPublicKey.publicJwk,
      {
        name: "RSA-OAEP",
        hash: "SHA-256",
      },
      true,
      ["encrypt"],
    );
    const wrappedFileKey = new Uint8Array(
      await crypto.subtle.encrypt(
        {
          name: "RSA-OAEP",
        },
        importedPublicKey,
        toArrayBuffer(fileKeyBytes),
      ),
    );
    recipientWrappedKeys.push({
      recipient_email: recipientEmail,
      wrapped_file_key: b64urlEncode(wrappedFileKey),
      algorithm: "RSA-OAEP-256",
    });
  }

  return {
    ciphertext,
    metadata: {
      encrypted: true,
      zero_knowledge: true,
      algorithm: "AES-256-GCM",
      schema_version: ZERO_KNOWLEDGE_SCHEMA_VERSION,
      iv: b64urlEncode(fileIvBytes),
      authentication_tag_appended: true,
      plaintext_size_bytes: plaintextBytes.byteLength,
      plaintext_sha256: b64urlEncode(plaintextSha),
      ciphertext_sha256: b64urlEncode(ciphertextSha),
      encryption_context: `vault:${vaultId}`,
      owner_wrapped_file_key: b64urlEncode(ownerWrappedFileKey),
      owner_wrap_algorithm: "AES-256-GCM",
      owner_wrap_kdf_algorithm: ZERO_KNOWLEDGE_KDF_ALGORITHM,
      owner_wrap_salt: ownerWrapSalt,
      owner_wrap_iv: b64urlEncode(ownerWrapIvBytes),
      recipient_wrapped_keys: recipientWrappedKeys,
    },
  };
}

export async function decryptVaultFile(
  ciphertext: ArrayBuffer | Uint8Array,
  {
    recoveryKey,
    vaultId,
    ownerWrappedFileKey,
    ownerWrapSalt,
    ownerWrapIv,
    iv,
  }: {
    recoveryKey: string;
    vaultId: string;
    ownerWrappedFileKey: string;
    ownerWrapSalt: string;
    ownerWrapIv: string;
    iv: string;
  },
): Promise<Uint8Array> {
  const crypto = assertCrypto();
  const ownerWrapKey = await deriveOwnerWrapKey(normalizeRecoveryKey(recoveryKey), {
    vaultId,
    ownerWrapSalt,
  });
  const fileKeyBytes = new Uint8Array(
    await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: toArrayBuffer(b64urlDecode(ownerWrapIv)),
      },
      ownerWrapKey,
      toArrayBuffer(b64urlDecode(ownerWrappedFileKey)),
    ),
  );
  const fileKey = await crypto.subtle.importKey(
    "raw",
    toArrayBuffer(fileKeyBytes),
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
  const plaintext = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: toArrayBuffer(b64urlDecode(iv)),
    },
    fileKey,
    toArrayBuffer(toUint8Array(ciphertext)),
  );
  return new Uint8Array(plaintext);
}

export async function decryptVaultFileForRecipient(
  ciphertext: ArrayBuffer | Uint8Array,
  {
    recipientPrivateKey,
    wrappedFileKey,
    iv,
  }: {
    recipientPrivateKey: CryptoKey;
    wrappedFileKey: string;
    iv: string;
  },
): Promise<Uint8Array> {
  const crypto = assertCrypto();
  const fileKeyBytes = new Uint8Array(
    await crypto.subtle.decrypt(
      {
        name: "RSA-OAEP",
      },
      recipientPrivateKey,
      toArrayBuffer(b64urlDecode(wrappedFileKey)),
    ),
  );
  const fileKey = await crypto.subtle.importKey(
    "raw",
    toArrayBuffer(fileKeyBytes),
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
  const plaintext = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: toArrayBuffer(b64urlDecode(iv)),
    },
    fileKey,
    toArrayBuffer(toUint8Array(ciphertext)),
  );
  return new Uint8Array(plaintext);
}

export async function buildRecipientWrappedKeysForVaultFile(
  {
    recoveryKey,
    vaultId,
    ownerWrappedFileKey,
    ownerWrapSalt,
    ownerWrapIv,
    recipientPublicKeys,
  }: {
    recoveryKey: string;
    vaultId: string;
    ownerWrappedFileKey: string;
    ownerWrapSalt: string;
    ownerWrapIv: string;
    recipientPublicKeys: Array<{ recipientEmail: string; publicJwk: JsonWebKey }>;
  },
): Promise<RecipientWrappedKeyAssignment[]> {
  const crypto = assertCrypto();
  const ownerWrapKey = await deriveOwnerWrapKey(normalizeRecoveryKey(recoveryKey), {
    vaultId,
    ownerWrapSalt,
  });
  const fileKeyBytes = new Uint8Array(
    await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: toArrayBuffer(b64urlDecode(ownerWrapIv)),
      },
      ownerWrapKey,
      toArrayBuffer(b64urlDecode(ownerWrappedFileKey)),
    ),
  );

  const wrappedKeys: RecipientWrappedKeyAssignment[] = [];
  for (const recipientPublicKey of recipientPublicKeys) {
    const recipientEmail = recipientPublicKey.recipientEmail.trim().toLowerCase();
    if (!recipientEmail) {
      continue;
    }
    const importedPublicKey = await crypto.subtle.importKey(
      "jwk",
      recipientPublicKey.publicJwk,
      {
        name: "RSA-OAEP",
        hash: "SHA-256",
      },
      true,
      ["encrypt"],
    );
    const wrappedFileKey = new Uint8Array(
      await crypto.subtle.encrypt(
        {
          name: "RSA-OAEP",
        },
        importedPublicKey,
        toArrayBuffer(fileKeyBytes),
      ),
    );
    wrappedKeys.push({
      recipient_email: recipientEmail,
      wrapped_file_key: b64urlEncode(wrappedFileKey),
      algorithm: "RSA-OAEP-256",
    });
  }

  return wrappedKeys;
}

export function storeRecoveryKeyForVault(vaultId: string, recoveryKey: string): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(
    `${RECOVERY_KEY_SESSION_PREFIX}${vaultId}`,
    normalizeRecoveryKey(recoveryKey),
  );
}

export function getStoredRecoveryKeyForVault(vaultId: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.sessionStorage.getItem(`${RECOVERY_KEY_SESSION_PREFIX}${vaultId}`);
}

export function clearStoredRecoveryKeyForVault(vaultId: string): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(`${RECOVERY_KEY_SESSION_PREFIX}${vaultId}`);
}

export function setRecoveryKeyBackupConfirmedForVault(vaultId: string): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(`${RECOVERY_KEY_BACKUP_CONFIRMED_PREFIX}${vaultId}`, "1");
}

export function hasConfirmedRecoveryKeyBackupForVault(vaultId: string): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.sessionStorage.getItem(`${RECOVERY_KEY_BACKUP_CONFIRMED_PREFIX}${vaultId}`) === "1";
}

export function clearRecoveryKeyBackupConfirmationForVault(vaultId: string): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(`${RECOVERY_KEY_BACKUP_CONFIRMED_PREFIX}${vaultId}`);
}

export function clearZeroKnowledgeSessionState(): void {
  if (typeof window === "undefined") {
    return;
  }

  const keysToRemove: string[] = [];
  for (let index = 0; index < window.sessionStorage.length; index += 1) {
    const key = window.sessionStorage.key(index);
    if (
      key?.startsWith(RECOVERY_KEY_SESSION_PREFIX) ||
      key?.startsWith(RECOVERY_KEY_BACKUP_CONFIRMED_PREFIX)
    ) {
      keysToRemove.push(key);
    }
  }

  for (const key of keysToRemove) {
    window.sessionStorage.removeItem(key);
  }
}

export function downloadRecoveryKeyBackup({
  recoveryKey,
  vaultId,
  vaultName,
}: {
  recoveryKey: string;
  vaultId: string;
  vaultName: string;
}): void {
  if (typeof window === "undefined") {
    return;
  }

  const fileContents = [
    "LastWrites Recovery Key Backup",
    "",
    `Vault Name: ${vaultName}`,
    `Vault ID: ${vaultId}`,
    `Saved At: ${new Date().toISOString()}`,
    "",
    "Recovery Key:",
    normalizeRecoveryKey(recoveryKey),
    "",
    "Warning:",
    "If you lose this recovery key, the server cannot recover zero-knowledge files for this vault.",
  ].join("\n");

  const blob = new Blob([fileContents], { type: "text/plain;charset=utf-8" });
  triggerBrowserDownload(blob, buildRecoveryKeyBackupFileName(vaultName, vaultId));
}
