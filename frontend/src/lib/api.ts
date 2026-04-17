export function getApiUrl(): string {
  return (process.env.NEXT_PUBLIC_API_URL || "").trim().replace(/\/$/, "");
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
