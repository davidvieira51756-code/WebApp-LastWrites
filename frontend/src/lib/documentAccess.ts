"use client";

import { b64urlDecode, b64urlEncode } from "@/lib/zeroKnowledge";

const DOCUMENT_PRIVATE_KEY_SESSION_PREFIX = "lw.docaccess.private.";
const DOCUMENT_PRIVATE_KEY_BUNDLE_SCHEMA_VERSION = "1";
const DOCUMENT_PRIVATE_KEY_WRAPPING_ALGORITHM = "AES-256-GCM";
const DOCUMENT_PRIVATE_KEY_KDF_ALGORITHM = "PBKDF2-SHA256";
const DOCUMENT_PRIVATE_KEY_DERIVE_ITERATIONS = 260000;

type CryptoProfileResponse = {
  initialized: boolean;
  encryption_public_jwk?: JsonWebKey | null;
  encrypted_private_key_bundle?: Record<string, string> | null;
};

type EncryptedPrivateKeyBundle = {
  schema_version: string;
  wrapping_algorithm: string;
  kdf_algorithm: string;
  salt: string;
  iv: string;
  ciphertext: string;
};

export type DocumentEncryptionProfilePayload = {
  encryption_public_jwk: JsonWebKey;
  encrypted_private_key_bundle: EncryptedPrivateKeyBundle;
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

function toArrayBuffer(value: Uint8Array): ArrayBuffer {
  return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) as ArrayBuffer;
}

function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function privateKeySessionStorageKey(email: string): string {
  return `${DOCUMENT_PRIVATE_KEY_SESSION_PREFIX}${normalizeEmail(email)}`;
}

async function derivePasswordWrappingKey(
  email: string,
  password: string,
  salt: Uint8Array,
): Promise<CryptoKey> {
  const crypto = assertCrypto();
  const baseKey = await crypto.subtle.importKey(
    "raw",
    toArrayBuffer(encodeText(`${normalizeEmail(email)}:${password}`)),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: toArrayBuffer(salt),
      iterations: DOCUMENT_PRIVATE_KEY_DERIVE_ITERATIONS,
      hash: "SHA-256",
    },
    baseKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

async function encryptPrivateKeyBundle(
  privateJwk: JsonWebKey,
  {
    email,
    password,
  }: {
    email: string;
    password: string;
  },
): Promise<EncryptedPrivateKeyBundle> {
  const crypto = assertCrypto();
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const wrappingKey = await derivePasswordWrappingKey(email, password, salt);
  const plaintextBytes = encodeText(JSON.stringify(privateJwk));
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt(
      {
        name: "AES-GCM",
        iv: toArrayBuffer(iv),
      },
      wrappingKey,
      toArrayBuffer(plaintextBytes),
    ),
  );

  return {
    schema_version: DOCUMENT_PRIVATE_KEY_BUNDLE_SCHEMA_VERSION,
    wrapping_algorithm: DOCUMENT_PRIVATE_KEY_WRAPPING_ALGORITHM,
    kdf_algorithm: DOCUMENT_PRIVATE_KEY_KDF_ALGORITHM,
    salt: b64urlEncode(salt),
    iv: b64urlEncode(iv),
    ciphertext: b64urlEncode(ciphertext),
  };
}

async function decryptPrivateKeyBundle(
  bundle: EncryptedPrivateKeyBundle,
  {
    email,
    password,
  }: {
    email: string;
    password: string;
  },
): Promise<JsonWebKey> {
  const crypto = assertCrypto();
  const wrappingKey = await derivePasswordWrappingKey(email, password, b64urlDecode(bundle.salt));
  const plaintext = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: toArrayBuffer(b64urlDecode(bundle.iv)),
    },
    wrappingKey,
    toArrayBuffer(b64urlDecode(bundle.ciphertext)),
  );
  return JSON.parse(new TextDecoder().decode(plaintext)) as JsonWebKey;
}

export async function createDocumentEncryptionProfilePayload(
  {
    email,
    password,
  }: {
    email: string;
    password: string;
  },
): Promise<DocumentEncryptionProfilePayload> {
  const crypto = assertCrypto();
  const keyPair = await crypto.subtle.generateKey(
    {
      name: "RSA-OAEP",
      modulusLength: 3072,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256",
    },
    true,
    ["encrypt", "decrypt"],
  );

  const publicJwk = (await crypto.subtle.exportKey("jwk", keyPair.publicKey)) as JsonWebKey;
  const privateJwk = (await crypto.subtle.exportKey("jwk", keyPair.privateKey)) as JsonWebKey;
  const normalizedPublicJwk: JsonWebKey = {
    ...publicJwk,
    alg: "RSA-OAEP-256",
    key_ops: ["encrypt"],
    ext: true,
  };
  const normalizedPrivateJwk: JsonWebKey = {
    ...privateJwk,
    alg: "RSA-OAEP-256",
    key_ops: ["decrypt"],
    ext: true,
  };

  return {
    encryption_public_jwk: normalizedPublicJwk,
    encrypted_private_key_bundle: await encryptPrivateKeyBundle(normalizedPrivateJwk, {
      email,
      password,
    }),
  };
}

function cachePrivateKeyJwk(email: string, privateJwk: JsonWebKey): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(privateKeySessionStorageKey(email), JSON.stringify(privateJwk));
}

export function clearDocumentAccessSessionState(): void {
  if (typeof window === "undefined") {
    return;
  }

  const keysToRemove: string[] = [];
  for (let index = 0; index < window.sessionStorage.length; index += 1) {
    const key = window.sessionStorage.key(index);
    if (key?.startsWith(DOCUMENT_PRIVATE_KEY_SESSION_PREFIX)) {
      keysToRemove.push(key);
    }
  }

  for (const key of keysToRemove) {
    window.sessionStorage.removeItem(key);
  }
}

export function hasCachedDocumentPrivateKey(email: string): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return Boolean(window.sessionStorage.getItem(privateKeySessionStorageKey(email)));
}

export async function getCachedDocumentPrivateKey(email: string): Promise<CryptoKey | null> {
  if (typeof window === "undefined") {
    return null;
  }

  const serialized = window.sessionStorage.getItem(privateKeySessionStorageKey(email));
  if (!serialized) {
    return null;
  }

  const crypto = assertCrypto();
  return crypto.subtle.importKey(
    "jwk",
    JSON.parse(serialized) as JsonWebKey,
    {
      name: "RSA-OAEP",
      hash: "SHA-256",
    },
    true,
    ["decrypt"],
  );
}

export async function ensureUserDocumentEncryptionProfile(
  apiUrl: string,
  authToken: string,
  {
    email,
    password,
  }: {
    email: string;
    password: string;
  },
): Promise<void> {
  const profileResponse = await fetch(`${apiUrl}/auth/crypto-profile`, {
    headers: {
      Authorization: `Bearer ${authToken}`,
    },
  });
  if (!profileResponse.ok) {
    throw new Error("Failed to load the document encryption profile.");
  }

  const existingProfile = (await profileResponse.json()) as CryptoProfileResponse;
  if (existingProfile.initialized && existingProfile.encrypted_private_key_bundle) {
    const privateJwk = await decryptPrivateKeyBundle(
      existingProfile.encrypted_private_key_bundle as EncryptedPrivateKeyBundle,
      {
        email,
        password,
      },
    );
    cachePrivateKeyJwk(email, privateJwk);
    return;
  }

  const createdProfile = await createDocumentEncryptionProfilePayload({
    email,
    password,
  });
  const normalizedPublicJwk = createdProfile.encryption_public_jwk;
  const encryptedPrivateKeyBundle = createdProfile.encrypted_private_key_bundle;

  const updateResponse = await fetch(`${apiUrl}/auth/crypto-profile`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${authToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      encryption_public_jwk: normalizedPublicJwk,
      encrypted_private_key_bundle: encryptedPrivateKeyBundle,
    }),
  });
  if (!updateResponse.ok) {
    throw new Error("Failed to initialize the document encryption profile.");
  }

  const privateJwk = await decryptPrivateKeyBundle(encryptedPrivateKeyBundle, { email, password });
  cachePrivateKeyJwk(email, privateJwk);
}

export async function getCurrentDocumentEncryptionPublicJwk(
  apiUrl: string,
  authToken: string,
): Promise<JsonWebKey> {
  const profileResponse = await fetch(`${apiUrl}/auth/crypto-profile`, {
    headers: {
      Authorization: `Bearer ${authToken}`,
    },
  });
  if (!profileResponse.ok) {
    throw new Error("Failed to load your document encryption profile.");
  }

  const profile = (await profileResponse.json()) as CryptoProfileResponse;
  if (!profile.initialized || !profile.encryption_public_jwk) {
    throw new Error("Your document encryption profile is not ready. Sign out and sign in again.");
  }
  return profile.encryption_public_jwk;
}
