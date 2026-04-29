function normalizeUrl(value: string): string {
  return value.trim().replace(/\/$/, "");
}

function tryDeriveAzureApiUrl(): string {
  if (typeof window === "undefined") {
    return "";
  }

  const currentHost = window.location.hostname.toLowerCase();
  const match = currentHost.match(/^web-(.+)\.azurewebsites\.net$/);
  if (!match) {
    return "";
  }

  return `https://api-${match[1]}.azurewebsites.net`;
}

function shouldUseConfiguredUrl(configuredUrl: string): boolean {
  if (!configuredUrl) {
    return false;
  }

  if (typeof window === "undefined") {
    return true;
  }

  const currentHost = window.location.hostname.toLowerCase();
  const configuredHost = (() => {
    try {
      return new URL(configuredUrl).hostname.toLowerCase();
    } catch {
      return "";
    }
  })();

  if (!configuredHost) {
    return false;
  }

  const currentIsLocal = /^(localhost|127\.0\.0\.1)$/.test(currentHost);
  const configuredIsLocal = /^(localhost|127\.0\.0\.1)$/.test(configuredHost);
  if (currentIsLocal || configuredIsLocal) {
    return currentIsLocal === configuredIsLocal;
  }

  const currentAzureMatch = currentHost.match(/^web-(.+)\.azurewebsites\.net$/);
  const configuredAzureMatch = configuredHost.match(/^api-(.+)\.azurewebsites\.net$/);
  if (currentAzureMatch && configuredAzureMatch) {
    return currentAzureMatch[1] === configuredAzureMatch[1];
  }

  return true;
}

export function getApiUrl(): string {
  const configuredUrl = normalizeUrl(process.env.NEXT_PUBLIC_API_URL || "");
  if (shouldUseConfiguredUrl(configuredUrl)) {
    return configuredUrl;
  }

  return tryDeriveAzureApiUrl();
}

export function buildAuthHeaders(
  token: string,
  includeJsonContentType: boolean
): HeadersInit {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
  };

  if (includeJsonContentType) {
    headers["Content-Type"] = "application/json";
  }

  return headers;
}

export async function getErrorDetail(
  response: Response,
  fallbackMessage: string
): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string; message?: string };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
    if (typeof payload.message === "string" && payload.message.trim()) {
      return payload.message;
    }
  } catch {
    return `${fallbackMessage} HTTP ${response.status}.`;
  }

  return fallbackMessage;
}

export function isUnauthorizedStatus(statusCode: number): boolean {
  return statusCode === 401 || statusCode === 403;
}
