export const AUTH_TOKEN_STORAGE_KEY = "lw.auth.token";
export const AUTH_EXPIRES_AT_STORAGE_KEY = "lw.auth.expires_at";
export const AUTH_EMAIL_STORAGE_KEY = "lw.auth.email";
export const AUTH_USER_ID_STORAGE_KEY = "lw.auth.user_id";

export const AUTH_TOKEN_COOKIE = "lw_auth_token";
export const AUTH_EXP_COOKIE = "lw_auth_exp";
export const POST_LOGIN_WARNING_STORAGE_KEY = "lw.post_login.warning";
const ZERO_KNOWLEDGE_RECOVERY_PREFIX = "lw.zk.recovery.";
const ZERO_KNOWLEDGE_BACKUP_PREFIX = "lw.zk.recovery.backed-up.";
const DOCUMENT_ACCESS_PRIVATE_KEY_PREFIX = "lw.docaccess.private.";

export type StoredAuthSession = {
  accessToken: string;
  expiresAt: string;
  email: string;
  userId: string;
};

function parseExpiryToEpoch(expiresAt: string): number | null {
  const timestamp = Date.parse(expiresAt);
  if (!Number.isFinite(timestamp)) {
    return null;
  }
  return Math.floor(timestamp / 1000);
}

function clearCookie(name: string): void {
  if (typeof document === "undefined") {
    return;
  }
  document.cookie = `${name}=; path=/; max-age=0; SameSite=Lax`;
}

function setCookie(name: string, value: string, maxAgeSeconds: number): void {
  if (typeof document === "undefined") {
    return;
  }

  const secureFlag = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${name}=${value}; path=/; max-age=${maxAgeSeconds}; SameSite=Lax${secureFlag}`;
}

export function clearAuthSession(): void {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(AUTH_EXPIRES_AT_STORAGE_KEY);
    window.localStorage.removeItem(AUTH_EMAIL_STORAGE_KEY);
    window.localStorage.removeItem(AUTH_USER_ID_STORAGE_KEY);
    window.localStorage.removeItem(POST_LOGIN_WARNING_STORAGE_KEY);

    const keysToRemove: string[] = [];
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (
        key?.startsWith(ZERO_KNOWLEDGE_RECOVERY_PREFIX) ||
        key?.startsWith(ZERO_KNOWLEDGE_BACKUP_PREFIX) ||
        key?.startsWith(DOCUMENT_ACCESS_PRIVATE_KEY_PREFIX)
      ) {
        keysToRemove.push(key);
      }
    }

    for (const key of keysToRemove) {
      window.sessionStorage.removeItem(key);
    }
  }

  clearCookie(AUTH_TOKEN_COOKIE);
  clearCookie(AUTH_EXP_COOKIE);
}

export function setAuthSession(session: StoredAuthSession): void {
  const expiryEpoch = parseExpiryToEpoch(session.expiresAt);
  if (expiryEpoch === null) {
    throw new Error("Invalid token expiration returned by API.");
  }

  const nowEpoch = Math.floor(Date.now() / 1000);
  const maxAgeSeconds = Math.max(0, expiryEpoch - nowEpoch);

  if (typeof window !== "undefined") {
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, session.accessToken);
    window.localStorage.setItem(AUTH_EXPIRES_AT_STORAGE_KEY, session.expiresAt);
    window.localStorage.setItem(AUTH_EMAIL_STORAGE_KEY, session.email);
    window.localStorage.setItem(AUTH_USER_ID_STORAGE_KEY, session.userId);
  }

  setCookie(AUTH_TOKEN_COOKIE, encodeURIComponent(session.accessToken), maxAgeSeconds);
  setCookie(AUTH_EXP_COOKIE, String(expiryEpoch), maxAgeSeconds);
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  const token = window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  const expiresAt = window.localStorage.getItem(AUTH_EXPIRES_AT_STORAGE_KEY);

  if (!token || !expiresAt) {
    return null;
  }

  const expiryEpoch = parseExpiryToEpoch(expiresAt);
  if (expiryEpoch === null || expiryEpoch <= Math.floor(Date.now() / 1000)) {
    clearAuthSession();
    return null;
  }

  return token;
}

export function getAuthEmail(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(AUTH_EMAIL_STORAGE_KEY);
}

export function getAuthUserId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(AUTH_USER_ID_STORAGE_KEY);
}

export function setPostLoginWarning(message: string): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(POST_LOGIN_WARNING_STORAGE_KEY, message);
}

export function consumePostLoginWarning(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const message = window.localStorage.getItem(POST_LOGIN_WARNING_STORAGE_KEY);
  if (message) {
    window.localStorage.removeItem(POST_LOGIN_WARNING_STORAGE_KEY);
  }
  return message;
}
