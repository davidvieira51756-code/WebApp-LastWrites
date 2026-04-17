"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    Alert,
    Badge,
    Button,
    ButtonLink,
    Card,
    Text,
    useCatTheme,
} from "@/components/catmagui";
import BrandLogo from "@/components/BrandLogo";
import { buildAuthHeaders, getApiUrl, getErrorDetail, isUnauthorizedStatus } from "@/lib/api";
import { clearAuthSession, getAuthEmail, getAuthToken } from "@/lib/auth";
import CreateVaultForm, { type Vault } from "../components/CreateVaultForm";

export default function DashboardPage() {
    const t = useCatTheme();
    const router = useRouter();
    const apiUrl = useMemo(() => getApiUrl(), []);

    const [vaults, setVaults] = useState<Vault[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [isCheckingAuth, setIsCheckingAuth] = useState(true);
    const [authToken, setAuthToken] = useState<string | null>(null);
    const [signedInEmail, setSignedInEmail] = useState<string | null>(null);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    const redirectToAuth = useCallback(() => {
        clearAuthSession();
        setAuthToken(null);
        router.replace("/auth?next=/");
    }, [router]);

    useEffect(() => {
        const token = getAuthToken();
        if (!token) {
            redirectToAuth();
            setIsCheckingAuth(false);
            return;
        }

        setAuthToken(token);
        setSignedInEmail(getAuthEmail());
        setIsCheckingAuth(false);
    }, [redirectToAuth]);

    const fetchVaults = useCallback(async () => {
        if (!apiUrl) {
            setErrorMessage("NEXT_PUBLIC_API_URL is not configured.");
            setVaults([]);
            return;
        }
        if (!authToken) {
            return;
        }

        setIsLoading(true);
        setErrorMessage(null);
        try {
            const response = await fetch(`${apiUrl}/vaults`, {
                method: "GET",
                headers: buildAuthHeaders(authToken, false),
            });

            if (isUnauthorizedStatus(response.status)) {
                redirectToAuth();
                return;
            }

            if (!response.ok) {
                const detail = await getErrorDetail(response, "Failed to fetch vaults.");
                throw new Error(detail);
            }

            const payload = (await response.json()) as Vault[];
            setVaults(Array.isArray(payload) ? payload : []);
        } catch (error) {
            const message = error instanceof Error ? error.message : "Unexpected fetch error.";
            setErrorMessage(message);
            setVaults([]);
        } finally {
            setIsLoading(false);
        }
    }, [apiUrl, authToken, redirectToAuth]);

    useEffect(() => {
        if (!isCheckingAuth && authToken) {
            void fetchVaults();
        }
    }, [authToken, fetchVaults, isCheckingAuth]);

    const handleVaultCreated = useCallback((createdVault: Vault) => {
        setVaults((previousVaults) => [
            createdVault,
            ...previousVaults.filter((vault) => vault.id !== createdVault.id),
        ]);
    }, []);

    const handleSignOut = useCallback(() => {
        clearAuthSession();
        setAuthToken(null);
        router.replace("/auth");
    }, [router]);

    const mainBackground = t.isDark
        ? "radial-gradient(circle at 15% 10%, rgba(216, 27, 96, 0.14), transparent 35%), radial-gradient(circle at 80% 8%, rgba(80, 80, 90, 0.32), transparent 30%), linear-gradient(180deg, #050505 0%, #09090B 60%, #050505 100%)"
        : "radial-gradient(circle at 15% 10%, rgba(216, 27, 96, 0.1), transparent 38%), radial-gradient(circle at 84% 10%, rgba(24, 24, 27, 0.06), transparent 35%), linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 55%, #FFFFFF 100%)";

    if (isCheckingAuth) {
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
                <div style={{ margin: "0 auto", width: "100%", maxWidth: 1180 }}>
                    <Card variant="elevated">
                        <Alert variant="info" message="Checking session..." />
                    </Card>
                </div>
            </main>
        );
    }

    if (!authToken) {
        return null;
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
            <div style={{ margin: "0 auto", width: "100%", maxWidth: 1180 }}>
                <BrandLogo marginBottom={0} />

                <header
                    style={{
                        marginBottom: t.space.xl,
                        display: "flex",
                        flexWrap: "wrap",
                        alignItems: "flex-end",
                        justifyContent: "space-between",
                        gap: t.space.m,
                    }}
                >
                    <div style={{ display: "flex", flexDirection: "column", gap: t.space.xs }}>
                        <Text variant="h1">Vault Dashboard</Text>
                        <Text variant="bodySmall" color="secondary" style={{ maxWidth: 700 }}>
                            Manage digital legacy vaults, monitor grace periods, and keep recipient delivery
                            rules up to date.
                        </Text>
                        {signedInEmail ? (
                            <Text variant="caption" color="muted">
                                Signed in as {signedInEmail}
                            </Text>
                        ) : null}
                    </div>

                    <div style={{ display: "flex", gap: t.space.xs, flexWrap: "wrap" }}>
                        <Button type="button" onClick={() => void fetchVaults()} variant="Primary">
                            Refresh Vaults
                        </Button>
                        <Button type="button" onClick={handleSignOut} variant="Destructive">
                            Sign Out
                        </Button>
                    </div>
                </header>

                {!apiUrl ? (
                    <Alert
                        variant="warning"
                        title="Missing API URL"
                        message="Configure NEXT_PUBLIC_API_URL in your frontend environment to connect to FastAPI."
                        style={{ marginBottom: t.space.m }}
                    />
                ) : null}

                <section
                    style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                        gap: t.space.m,
                        alignItems: "start",
                    }}
                >
                    <CreateVaultForm
                        apiUrl={apiUrl}
                        authToken={authToken}
                        onCreated={handleVaultCreated}
                        onUnauthorized={redirectToAuth}
                    />

                    <Card variant="elevated" style={{ gap: t.space.s }}>
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "space-between",
                                gap: t.space.s,
                            }}
                        >
                            <Text variant="h3">Existing Vaults</Text>
                            <Badge label={`${vaults.length} total`} variant="default" size="sm" outlineOnly />
                        </div>

                        {isLoading ? (
                            <Alert variant="info" message="Loading vaults..." />
                        ) : null}

                        {errorMessage ? (
                            <Alert variant="error" message={errorMessage} />
                        ) : null}

                        {!isLoading && !errorMessage && vaults.length === 0 ? (
                            <Alert variant="info" message="No vaults found." />
                        ) : null}

                        <div style={{ display: "flex", flexDirection: "column", gap: t.space.s }}>
                            {vaults.map((vault) => (
                                <Card
                                    key={vault.id}
                                    variant="secondary"
                                    style={{
                                        gap: t.space.xs,
                                        padding: t.space.m,
                                    }}
                                >
                                    <div
                                        style={{
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "flex-start",
                                            gap: t.space.s,
                                            flexWrap: "wrap",
                                        }}
                                    >
                                        <div style={{ display: "flex", flexDirection: "column", gap: t.space.xxs }}>
                                            <Text variant="label" weight="semibold">
                                                {vault.name}
                                            </Text>
                                            <Text variant="caption" color="muted">
                                                Vault ID: {vault.id}
                                            </Text>
                                        </div>
                                        <Badge label={vault.status} variant="success" size="sm" />
                                    </div>

                                    <div
                                        style={{
                                            display: "grid",
                                            gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                                            gap: t.space.xs,
                                        }}
                                    >
                                        <Text variant="bodySmall" color="secondary">
                                            Grace Period: {vault.grace_period_days} days
                                        </Text>
                                        <Text variant="bodySmall" color="secondary">
                                            Recipients: {vault.recipients.length}
                                        </Text>
                                    </div>

                                    <div style={{ marginTop: t.space.xxs }}>
                                        <ButtonLink
                                            href={`/vaults/${encodeURIComponent(vault.id)}`}
                                            variant="Primary"
                                            size="default"
                                        >
                                            Open Vault
                                        </ButtonLink>
                                    </div>
                                </Card>
                            ))}
                        </div>
                    </Card>
                </section>
            </div>
        </main>
    );
}
