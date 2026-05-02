"use client";

import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Alert, Button, ButtonLink, Card, Input, Text, useCatTheme } from "@/components/catmagui";
import BrandLogo from "@/components/BrandLogo";
import { buildAuthHeaders, getApiUrl, getErrorDetail, isUnauthorizedStatus } from "@/lib/api";
import { clearAuthSession, getAuthToken } from "@/lib/auth";

type ProfileResponse = {
  user_id: string;
  email: string;
  email_verified: boolean;
  username: string;
  full_name: string;
  birth_date: string;
  display_name_preference: "username" | "real_name";
};

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

export default function ProfilePage() {
  const t = useCatTheme();
  const router = useRouter();
  const apiUrl = useMemo(() => getApiUrl(), []);

  const [authToken, setAuthToken] = useState<string | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [isLoading, setIsLoading] = useState(true);

  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [displayNamePreference, setDisplayNamePreference] = useState<"username" | "real_name">("username");

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [deletePassword, setDeletePassword] = useState("");

  const [profileMessage, setProfileMessage] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [isDeletingAccount, setIsDeletingAccount] = useState(false);

  const redirectToAuth = useCallback(() => {
    clearAuthSession();
    setAuthToken(null);
    router.replace("/auth?next=/profile");
  }, [router]);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      redirectToAuth();
      setIsCheckingAuth(false);
      return;
    }
    setAuthToken(token);
    setIsCheckingAuth(false);
  }, [redirectToAuth]);

  const fetchProfile = useCallback(async () => {
    if (!apiUrl || !authToken) {
      return;
    }

    setIsLoading(true);
    setProfileError(null);
    try {
      const response = await fetch(`${apiUrl}/auth/me`, {
        headers: buildAuthHeaders(authToken, false),
      });
      if (isUnauthorizedStatus(response.status)) {
        redirectToAuth();
        return;
      }
      if (!response.ok) {
        throw new Error(await getErrorDetail(response, "Failed to load profile."));
      }

      const payload = (await response.json()) as ProfileResponse;
      setEmail(payload.email);
      setUsername(payload.username);
      setFullName(payload.full_name);
      setBirthDate(payload.birth_date);
      setDisplayNamePreference(payload.display_name_preference);
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : "Unexpected profile load error.");
    } finally {
      setIsLoading(false);
    }
  }, [apiUrl, authToken, redirectToAuth]);

  useEffect(() => {
    if (!isCheckingAuth && authToken) {
      void fetchProfile();
    }
  }, [authToken, fetchProfile, isCheckingAuth]);

  const handleProfileSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setProfileMessage(null);
    setProfileError(null);

    if (!apiUrl || !authToken) {
      setProfileError("Authentication is required.");
      return;
    }
    if (!USERNAME_REGEX.test(username.trim())) {
      setProfileError("Username must be 3-32 characters and use only letters, numbers, or underscores.");
      return;
    }
    if (!fullName.trim()) {
      setProfileError("Full name is required.");
      return;
    }
    if (!birthDate || !isAtLeast13YearsOld(birthDate)) {
      setProfileError("You must be at least 13 years old.");
      return;
    }

    setIsSavingProfile(true);
    try {
      const response = await fetch(`${apiUrl}/auth/me`, {
        method: "PATCH",
        headers: buildAuthHeaders(authToken, true),
        body: JSON.stringify({
          username: username.trim().toLowerCase(),
          full_name: fullName.trim(),
          birth_date: birthDate,
          display_name_preference: displayNamePreference,
        }),
      });
      if (isUnauthorizedStatus(response.status)) {
        redirectToAuth();
        return;
      }
      if (!response.ok) {
        throw new Error(await getErrorDetail(response, "Failed to update profile."));
      }

      setProfileMessage("Profile updated successfully.");
      await fetchProfile();
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : "Unexpected profile update error.");
    } finally {
      setIsSavingProfile(false);
    }
  };

  const handlePasswordSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPasswordMessage(null);
    setPasswordError(null);

    if (!apiUrl || !authToken) {
      setPasswordError("Authentication is required.");
      return;
    }
    if (newPassword.length < 8) {
      setPasswordError("Password must be at least 8 characters.");
      return;
    }

    setIsChangingPassword(true);
    try {
      const response = await fetch(`${apiUrl}/auth/change-password`, {
        method: "POST",
        headers: buildAuthHeaders(authToken, true),
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      if (isUnauthorizedStatus(response.status)) {
        redirectToAuth();
        return;
      }
      if (!response.ok) {
        throw new Error(await getErrorDetail(response, "Failed to change password."));
      }

      setCurrentPassword("");
      setNewPassword("");
      setPasswordMessage("Password updated successfully.");
    } catch (error) {
      setPasswordError(error instanceof Error ? error.message : "Unexpected password update error.");
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleDeleteAccount = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setDeleteError(null);

    if (!apiUrl || !authToken) {
      setDeleteError("Authentication is required.");
      return;
    }
    if (!deletePassword) {
      setDeleteError("Password is required.");
      return;
    }

    setIsDeletingAccount(true);
    try {
      const response = await fetch(`${apiUrl}/auth/me`, {
        method: "DELETE",
        headers: buildAuthHeaders(authToken, true),
        body: JSON.stringify({ password: deletePassword }),
      });
      if (isUnauthorizedStatus(response.status)) {
        redirectToAuth();
        return;
      }
      if (!response.ok) {
        throw new Error(await getErrorDetail(response, "Failed to delete account."));
      }

      clearAuthSession();
      router.replace("/auth");
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Unexpected account deletion error.");
    } finally {
      setIsDeletingAccount(false);
    }
  };

  const mainBackground = t.isDark
    ? "radial-gradient(circle at 15% 10%, rgba(216, 27, 96, 0.14), transparent 35%), radial-gradient(circle at 80% 8%, rgba(80, 80, 90, 0.32), transparent 30%), linear-gradient(180deg, #050505 0%, #09090B 60%, #050505 100%)"
    : "radial-gradient(circle at 15% 10%, rgba(216, 27, 96, 0.1), transparent 38%), radial-gradient(circle at 84% 10%, rgba(24, 24, 27, 0.06), transparent 35%), linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 55%, #FFFFFF 100%)";

  if (isCheckingAuth || !authToken) {
    return <main style={{ minHeight: "100vh", background: mainBackground }} />;
  }

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
      <div style={{ margin: "0 auto", width: "100%", maxWidth: 980, display: "grid", gap: t.space.m }}>
        <BrandLogo marginBottom={0} />

        <Card variant="elevated" style={{ gap: t.space.s }}>
          <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: t.space.s }}>
            <div style={{ display: "grid", gap: t.space.xxs }}>
              <Text variant="h2">Profile</Text>
              <Text variant="bodySmall" color="secondary">
                Manage the name other users see, update your password, or delete your account.
              </Text>
              {email ? <Text variant="caption" color="muted">Signed in as {email}</Text> : null}
            </div>
            <ButtonLink href="/" variant="Primary">Back to Dashboard</ButtonLink>
          </div>
        </Card>

        {isLoading ? <Alert variant="info" message="Loading profile..." /> : null}
        {profileError && isLoading ? <Alert variant="error" message={profileError} /> : null}

        {!isLoading ? (
          <section
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
              gap: t.space.m,
              alignItems: "start",
            }}
          >
            <Card variant="elevated" style={{ gap: t.space.s }}>
              <Text variant="h3">Public Profile</Text>
              <form onSubmit={handleProfileSubmit} style={{ display: "grid", gap: t.space.s }}>
                <Input id="profile-email" label="Email" value={email} disabled />
                <Input id="profile-username" label="Username" value={username} onChange={(event) => setUsername(event.target.value)} required />
                <Input id="profile-full-name" label="Full Name" value={fullName} onChange={(event) => setFullName(event.target.value)} required />
                <Input id="profile-birth-date" type="date" label="Birth Date" value={birthDate} onChange={(event) => setBirthDate(event.target.value)} required />

                <div style={{ display: "grid", gap: t.space.xs }}>
                  <Text variant="label">Display Name For Recipients</Text>
                  <label style={{ display: "flex", gap: t.space.xs, alignItems: "center" }}>
                    <input
                      type="radio"
                      name="display-name-preference"
                      checked={displayNamePreference === "username"}
                      onChange={() => setDisplayNamePreference("username")}
                    />
                    <Text variant="bodySmall">Username</Text>
                  </label>
                  <label style={{ display: "flex", gap: t.space.xs, alignItems: "center" }}>
                    <input
                      type="radio"
                      name="display-name-preference"
                      checked={displayNamePreference === "real_name"}
                      onChange={() => setDisplayNamePreference("real_name")}
                    />
                    <Text variant="bodySmall">Real name</Text>
                  </label>
                </div>

                <Button type="submit" size="full" variant="SolidPrimary" disabled={isSavingProfile}>
                  {isSavingProfile ? "Saving..." : "Save Profile"}
                </Button>
              </form>

              {profileError ? <Alert variant="error" message={profileError} /> : null}
              {profileMessage ? <Alert variant="success" message={profileMessage} /> : null}
            </Card>

            <div style={{ display: "grid", gap: t.space.m }}>
              <Card variant="elevated" style={{ gap: t.space.s }}>
                <Text variant="h3">Change Password</Text>
                <form onSubmit={handlePasswordSubmit} style={{ display: "grid", gap: t.space.s }}>
                  <Input id="current-password" type="password" label="Current Password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required />
                  <Input id="new-password" type="password" label="New Password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
                  <Button type="submit" size="full" variant="SolidPrimary" disabled={isChangingPassword}>
                    {isChangingPassword ? "Updating Password..." : "Update Password"}
                  </Button>
                </form>

                {passwordError ? <Alert variant="error" message={passwordError} /> : null}
                {passwordMessage ? <Alert variant="success" message={passwordMessage} /> : null}
              </Card>

              <Card variant="elevated" style={{ gap: t.space.s }}>
                <Text variant="h3">Delete Account</Text>
                <Text variant="bodySmall" color="secondary">
                  This removes your account and deletes vaults you own.
                </Text>
                <form onSubmit={handleDeleteAccount} style={{ display: "grid", gap: t.space.s }}>
                  <Input id="delete-password" type="password" label="Password" value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} required />
                  <Button type="submit" size="full" variant="Destructive" disabled={isDeletingAccount}>
                    {isDeletingAccount ? "Deleting Account..." : "Delete Account"}
                  </Button>
                </form>

                {deleteError ? <Alert variant="error" message={deleteError} /> : null}
              </Card>
            </div>
          </section>
        ) : null}
      </div>
    </main>
  );
}
