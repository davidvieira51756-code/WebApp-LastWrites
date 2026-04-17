"use client";

import { useRouter, useSearchParams } from "next/navigation";
import type { FormEvent } from "react";
import { Suspense, useEffect, useMemo, useState } from "react";

import { Alert, Button, Card, Input, Text, useCatTheme } from "@/components/catmagui";
import BrandLogo from "@/components/BrandLogo";
import { getApiUrl, getErrorDetail } from "@/lib/api";
import { getAuthToken, setAuthSession } from "@/lib/auth";

type AuthMode = "signin" | "signup";

type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_at: string;
  user_id: string;
  email: string;
  email_verified: boolean;
};

type RegisterResponse = {
  message: string;
  user_id: string;
  email: string;
  email_verification_required: boolean;
  verification_url: string;
  verification_token?: string | null;
};

const EMAIL_REGEX =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;

function AuthPageContent() {
  const t = useCatTheme();
  const router = useRouter();
  const searchParams = useSearchParams();

  const apiUrl = useMemo(() => getApiUrl(), []);
  const nextPath = useMemo(() => {
    const candidate = searchParams.get("next") || "/";
    if (!candidate.startsWith("/")) {
      return "/";
    }
    return candidate;
  }, [searchParams]);

  const [mode, setMode] = useState<AuthMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [verificationUrl, setVerificationUrl] = useState<string | null>(null);
  const [verificationToken, setVerificationToken] = useState<string | null>(null);

  useEffect(() => {
    const token = getAuthToken();
    if (token) {
      router.replace(nextPath);
    }
  }, [nextPath, router]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);
    setSuccessMessage(null);
    setVerificationUrl(null);
    setVerificationToken(null);

    if (!apiUrl) {
      setErrorMessage("NEXT_PUBLIC_API_URL is not configured.");
      return;
    }

    const normalizedEmail = email.trim().toLowerCase();
    if (!EMAIL_REGEX.test(normalizedEmail)) {
      setErrorMessage("Please provide a valid email address.");
      return;
    }

    if (!password) {
      setErrorMessage("Password is required.");
      return;
    }

    if (mode === "signup") {
      if (password.length < 8) {
        setErrorMessage("Password must be at least 8 characters.");
        return;
      }
      if (password !== confirmPassword) {
        setErrorMessage("Password confirmation does not match.");
        return;
      }
    }

    setIsSubmitting(true);
    try {
      if (mode === "signin") {
        const response = await fetch(`${apiUrl}/auth/login`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ email: normalizedEmail, password }),
        });

        if (!response.ok) {
          const detail = await getErrorDetail(response, "Sign in failed.");
          throw new Error(detail);
        }

        const payload = (await response.json()) as LoginResponse;
        setAuthSession({
          accessToken: payload.access_token,
          expiresAt: payload.expires_at,
          email: payload.email,
          userId: payload.user_id,
        });
        router.replace(nextPath);
        return;
      }

      const response = await fetch(`${apiUrl}/auth/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email: normalizedEmail, password }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(response, "Sign up failed.");
        throw new Error(detail);
      }

      const payload = (await response.json()) as RegisterResponse;
      setSuccessMessage(payload.message || "Account created successfully.");
      setVerificationUrl(payload.verification_url || null);
      setVerificationToken(payload.verification_token || null);
      setMode("signin");
      setPassword("");
      setConfirmPassword("");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected authentication error.";
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
      <div style={{ margin: "0 auto", width: "100%", maxWidth: 920, display: "grid", gap: t.space.m }}>
        <BrandLogo marginBottom={t.space.xs} />

        <Card variant="elevated" style={{ gap: t.space.s }}>
          <Text variant="h1">Sign In Or Create Account</Text>
          <Text variant="bodySmall" color="secondary" style={{ maxWidth: 640 }}>
            Protect vault access using email and password authentication. New accounts require
            email verification before sign in.
          </Text>
        </Card>

        <section
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: t.space.m,
            alignItems: "start",
          }}
        >
          <Card variant="elevated" style={{ gap: t.space.s }}>
            <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
              <Button
                type="button"
                variant={mode === "signin" ? "SolidPrimary" : "Primary"}
                onClick={() => {
                  setMode("signin");
                  setErrorMessage(null);
                  setSuccessMessage(null);
                }}
              >
                Sign In
              </Button>
              <Button
                type="button"
                variant={mode === "signup" ? "SolidPrimary" : "Primary"}
                onClick={() => {
                  setMode("signup");
                  setErrorMessage(null);
                  setSuccessMessage(null);
                }}
              >
                Sign Up
              </Button>
            </div>

            <form
              onSubmit={handleSubmit}
              style={{ display: "flex", flexDirection: "column", gap: t.space.s }}
            >
              <Input
                id="auth-email"
                type="email"
                label="Email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                required
              />

              <Input
                id="auth-password"
                type="password"
                label="Password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="At least 8 characters"
                required
              />

              {mode === "signup" ? (
                <Input
                  id="auth-confirm-password"
                  type="password"
                  label="Confirm Password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  placeholder="Repeat password"
                  required
                />
              ) : null}

              <Button type="submit" size="full" variant="SolidPrimary" disabled={isSubmitting}>
                {isSubmitting
                  ? mode === "signin"
                    ? "Signing In..."
                    : "Creating Account..."
                  : mode === "signin"
                    ? "Sign In"
                    : "Create Account"}
              </Button>
            </form>

            {errorMessage ? <Alert variant="error" message={errorMessage} /> : null}
            {successMessage ? <Alert variant="success" message={successMessage} /> : null}
          </Card>

          <Card variant="secondary" style={{ gap: t.space.s }}>
            <Text variant="h3">Email Verification</Text>
            <Text variant="bodySmall" color="secondary">
              After sign up, open your verification link. In local development, you can use the
              generated token directly.
            </Text>

            {verificationUrl ? (
              <Alert
                variant="info"
                title="Verification URL"
                message={verificationUrl}
                style={{ wordBreak: "break-all" }}
              />
            ) : null}

            {verificationToken ? (
              <Alert
                variant="warning"
                title="Local Development Token"
                message={verificationToken}
                style={{ wordBreak: "break-all" }}
              />
            ) : null}

            <Button
              type="button"
              variant="Primary"
              onClick={() => {
                if (verificationToken) {
                  router.push(`/verify-email?token=${encodeURIComponent(verificationToken)}`);
                  return;
                }
                router.push("/verify-email");
              }}
            >
              Open Verification Page
            </Button>

            <Text variant="caption" color="muted">
              Next path after sign in: {nextPath}
            </Text>
          </Card>
        </section>
      </div>
    </main>
  );
}

export default function AuthPage() {
  return (
    <Suspense fallback={<main style={{ minHeight: "100vh" }} />}>
      <AuthPageContent />
    </Suspense>
  );
}
