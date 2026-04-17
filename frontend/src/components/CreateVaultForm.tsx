"use client";

import type { FormEvent } from "react";
import { useState } from "react";

import { Alert, Button, Card, Input, Text, useCatTheme } from "@/components/catmagui";
import { buildAuthHeaders, isUnauthorizedStatus } from "@/lib/api";

export type Vault = {
    id: string;
    user_id: string;
    name: string;
    grace_period_days: number;
    status: string;
    recipients: string[];
    files?: Array<Record<string, unknown>>;
};

type CreateVaultFormProps = {
    apiUrl: string;
    authToken: string;
    onCreated: (vault: Vault) => void;
    onUnauthorized?: () => void;
};

export default function CreateVaultForm({
    apiUrl,
    authToken,
    onCreated,
    onUnauthorized,
}: CreateVaultFormProps) {
    const t = useCatTheme();
    const [name, setName] = useState("");
    const [gracePeriodDays, setGracePeriodDays] = useState(30);
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

        if (!authToken) {
            setErrorMessage("You must be signed in to create a vault.");
            return;
        }

        const normalizedName = name.trim();
        if (!normalizedName) {
            setErrorMessage("Vault name is required.");
            return;
        }

        setIsSubmitting(true);
        try {
            const response = await fetch(`${apiUrl}/vaults`, {
                method: "POST",
                headers: buildAuthHeaders(authToken, true),
                body: JSON.stringify({
                    name: normalizedName,
                    grace_period_days: Number(gracePeriodDays),
                    status: "active",
                    recipients: [],
                }),
            });

            if (isUnauthorizedStatus(response.status)) {
                onUnauthorized?.();
                return;
            }

            if (!response.ok) {
                let detail = "Failed to create vault.";
                try {
                    const errorPayload = (await response.json()) as { detail?: string };
                    if (errorPayload.detail) {
                        detail = errorPayload.detail;
                    }
                } catch {
                    detail = `Failed to create vault. HTTP ${response.status}.`;
                }
                throw new Error(detail);
            }

            const createdVault = (await response.json()) as Vault;
            onCreated(createdVault);
            setName("");
            setGracePeriodDays(30);
            setSuccessMessage("Vault created successfully.");
        } catch (error) {
            const message =
                error instanceof Error ? error.message : "Unexpected error while creating vault.";
            setErrorMessage(message);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <Card
            variant="elevated"
            style={{
                gap: t.space.m,
            }}
        >
            <Text variant="h3">Create New Vault</Text>
            <Text variant="bodySmall" color="secondary">
                Define a secure vault and set the grace period for delivery activation.
            </Text>

            <form
                onSubmit={handleSubmit}
                style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: t.space.s,
                    marginTop: t.space.s,
                }}
            >
                <Input
                    id="vault-name"
                    label="Vault Name"
                    type="text"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Family Legacy Vault"
                    required
                />

                <Input
                    id="grace-period"
                    label="Grace Period (days)"
                    type="number"
                    min={1}
                    max={3650}
                    value={gracePeriodDays}
                    onChange={(event) => setGracePeriodDays(Number(event.target.value))}
                    required
                />

                <Button
                    type="submit"
                    size="full"
                    variant="SolidPrimary"
                    disabled={isSubmitting}
                >
                    {isSubmitting ? "Creating Vault..." : "Create Vault"}
                </Button>
            </form>

            {errorMessage ? (
                <Alert message={errorMessage} variant="error" />
            ) : null}

            {successMessage ? (
                <Alert message={successMessage} variant="success" />
            ) : null}
        </Card>
    );
}
