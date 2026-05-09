"use client";

import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useMemo, useState } from "react";

import { Alert, Button, Card, Input, Text, useCatTheme } from "@/components/catmagui";
import BrandLogo from "@/components/BrandLogo";
import { getApiUrl, getErrorDetail } from "@/lib/api";

function ForgotPasswordPageContent() {
  const t = useCatTheme();
  const router = useRouter();
  const apiUrl = useMemo(() => getApiUrl(), []);

  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);
    setSuccessMessage(null);

    if (!apiUrl) {
      setErrorMessage("NEXT_PUBLIC_API_URL is not configured.");
      return;
    }

    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail) {
      setErrorMessage("A valid email address is required.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch(`${apiUrl}/auth/request-password-reset`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email: normalizedEmail }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(response, "Failed to request password reset.");
        throw new Error(detail);
      }

      setSuccessMessage(
        "If the email exists, a password reset link has been sent. Check your inbox.",
      );
      setEmail("");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected password reset request error.";
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
          <Text variant="h2">Forgot Password</Text>
          <Text variant="bodySmall" color="secondary">
            Enter your email address and we will send a link to reset your password.
          </Text>
        </Card>

        <Card variant="elevated" style={{ gap: t.space.s }}>
          <form
            onSubmit={handleSubmit}
            style={{ display: "flex", flexDirection: "column", gap: t.space.s }}
          >
            <Input
              id="forgot-password-email"
              label="Email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              required
            />

            <Button type="submit" size="full" variant="SolidPrimary" disabled={isSubmitting}>
              {isSubmitting ? "Sending reset email..." : "Send password reset email"}
            </Button>
          </form>

          {errorMessage ? <Alert variant="error" message={errorMessage} /> : null}
          {successMessage ? <Alert variant="success" message={successMessage} /> : null}

          <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
            <Button type="button" variant="Primary" onClick={() => router.push("/auth") }>
              Back to Sign In
            </Button>
          </div>
        </Card>
      </div>
    </main>
  );
}

export default function ForgotPasswordPage() {
  return <ForgotPasswordPageContent />;
}
