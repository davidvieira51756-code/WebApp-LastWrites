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
  username: string;
  email_verification_required: boolean;
};

const EMAIL_REGEX =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;
const USERNAME_REGEX = /^[A-Za-z0-9_]{3,32}$/;

function isAtLeast13YearsOld(birthDate: string): boolean {
  const parsedBirthDate = new Date(`${birthDate}T00:00:00`);
  if (Number.isNaN(parsedBirthDate.getTime())) {
    return false;
  }

  const today = new Date();
  let age = today.getFullYear() - parsedBirthDate.getFullYear();
  const monthDifference = today.getMonth() - parsedBirthDate.getMonth();
  if (
    monthDifference < 0 ||
    (monthDifference === 0 && today.getDate() < parsedBirthDate.getDate())
  ) {
    age -= 1;
  }
  return age >= 13;
}

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
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

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
      if (!USERNAME_REGEX.test(username.trim().toLowerCase())) {
        setErrorMessage("Username must be 3-32 characters and use only letters, numbers, or underscores.");
        return;
      }
      if (!fullName.trim()) {
        setErrorMessage("Full name is required.");
        return;
      }
      if (!birthDate) {
        setErrorMessage("Birth date is required.");
        return;
      }
      if (!isAtLeast13YearsOld(birthDate)) {
        setErrorMessage("You must be at least 13 years old.");
        return;
      }
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
        body: JSON.stringify({
          email: normalizedEmail,
          username: username.trim().toLowerCase(),
          full_name: fullName.trim(),
          birth_date: birthDate,
          password,
        }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(response, "Sign up failed.");
        throw new Error(detail);
      }

      const payload = (await response.json()) as RegisterResponse;
      setSuccessMessage(
        payload.message || "Account created successfully. Check your email for the verification link.",
      );
      setMode("signin");
      setUsername("");
      setFullName("");
      setBirthDate("");
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
            gridTemplateColumns: "minmax(320px, 1fr)",
            gap: t.space.m,
            alignItems: "start",
            justifyItems: "center",
          }}
        >
          <Card variant="elevated" style={{ gap: t.space.s, width: "100%", maxWidth: 520 }}>
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

              {mode === "signup" ? (
                <>
                  <Input
                    id="auth-username"
                    type="text"
                    label="Username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="your_username"
                    required
                  />

                  <Input
                    id="auth-full-name"
                    type="text"
                    label="Full Name"
                    value={fullName}
                    onChange={(event) => setFullName(event.target.value)}
                    placeholder="Your full name"
                    required
                  />

                  <Input
                    id="auth-birth-date"
                    type="date"
                    label="Birth Date"
                    value={birthDate}
                    onChange={(event) => setBirthDate(event.target.value)}
                    required
                  />
                </>
              ) : null}

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
