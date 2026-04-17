"use client";

import { useRouter, useSearchParams } from "next/navigation";
import type { FormEvent } from "react";
import { Suspense, useMemo, useState } from "react";

import { Alert, Button, Card, Input, Text, useCatTheme } from "@/components/catmagui";
import BrandLogo from "@/components/BrandLogo";
import { getApiUrl, getErrorDetail } from "@/lib/api";

function VerifyEmailPageContent() {
  const t = useCatTheme();
  const router = useRouter();
  const searchParams = useSearchParams();

  const apiUrl = useMemo(() => getApiUrl(), []);
  const initialToken = useMemo(() => searchParams.get("token") || "", [searchParams]);

  const [token, setToken] = useState(initialToken);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const handleVerify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);
    setSuccessMessage(null);

    if (!apiUrl) {
      setErrorMessage("NEXT_PUBLIC_API_URL is not configured.");
      return;
    }

    const normalizedToken = token.trim();
    if (!normalizedToken) {
      setErrorMessage("Verification token is required.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch(`${apiUrl}/auth/verify-email`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ token: normalizedToken }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(response, "Email verification failed.");
        throw new Error(detail);
      }

      setSuccessMessage("Email verified successfully. You can now sign in.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected verification error.";
      setErrorMessage(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const mainBackground = t.isDark
    ? "radial-gradient(circle at 20% 10%, rgba(216, 27, 96, 0.16), transparent 36%), radial-gradient(circle at 80% 4%, rgba(80, 80, 90, 0.32), transparent 32%), linear-gradient(180deg, #050505 0%, #09090B 60%, #050505 100%)"
    : "radial-gradient(circle at 15% 10%, rgba(216, 27, 96, 0.1), transparent 40%), radial-gradient(circle at 86% 8%, rgba(24, 24, 27, 0.06), transparent 36%), linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 55%, #FFFFFF 100%)";

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
      <div style={{ margin: "0 auto", width: "100%", maxWidth: 760, display: "grid", gap: t.space.m }}>
        <BrandLogo marginBottom={t.space.xxs} />

        <Card variant="elevated" style={{ gap: t.space.s }}>
          <Text variant="h2">Verify Email Address</Text>
          <Text variant="bodySmall" color="secondary">
            Paste your verification token and confirm your account before signing in.
          </Text>
        </Card>

        <Card variant="elevated" style={{ gap: t.space.s }}>
          <form
            onSubmit={handleVerify}
            style={{ display: "flex", flexDirection: "column", gap: t.space.s }}
          >
            <Input
              id="verification-token"
              label="Verification Token"
              type="text"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="Paste token"
              required
            />

            <Button type="submit" size="full" variant="SolidPrimary" disabled={isSubmitting}>
              {isSubmitting ? "Verifying..." : "Verify Email"}
            </Button>
          </form>

          {errorMessage ? <Alert variant="error" message={errorMessage} /> : null}
          {successMessage ? <Alert variant="success" message={successMessage} /> : null}

          <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
            <Button type="button" variant="Primary" onClick={() => router.push("/auth")}>
              Back To Sign In
            </Button>
          </div>
        </Card>
      </div>
    </main>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<main style={{ minHeight: "100vh" }} />}>
      <VerifyEmailPageContent />
    </Suspense>
  );
}
