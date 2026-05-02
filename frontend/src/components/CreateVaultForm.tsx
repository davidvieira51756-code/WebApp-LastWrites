"use client";

import type { FormEvent } from "react";
import { useState } from "react";

import { Alert, Button, Card, Input, Text, useCatTheme } from "@/components/catmagui";
import { buildAuthHeaders, isUnauthorizedStatus } from "@/lib/api";

export type ActivationRequestItem = {
    recipient_email: string;
    requested_at: string;
    reason?: string | null;
};

export type VaultRecipient = {
    email: string;
    can_activate: boolean;
};

export type Vault = {
    id: string;
    user_id: string;
    name: string;
    owner_message?: string | null;
    grace_period_days: number;
    grace_period_value?: number;
    grace_period_unit?: "days" | "hours";
    grace_period_hours?: number;
    status: string;
    recipients: VaultRecipient[];
    files?: Array<Record<string, unknown>>;
    activation_threshold?: number;
    activation_requests?: ActivationRequestItem[];
    grace_period_started_at?: string | null;
    grace_period_expires_at?: string | null;
    last_check_in_at?: string | null;
    delivery_blob_name?: string | null;
    delivery_container_name?: string | null;
    delivery_file_name?: string | null;
    delivered_at?: string | null;
    delivery_error?: string | null;
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
    const [ownerMessage, setOwnerMessage] = useState("");
    const [gracePeriodValue, setGracePeriodValue] = useState(30);
    const [gracePeriodUnit, setGracePeriodUnit] = useState<"days" | "hours">("days");
    const [activationThreshold, setActivationThreshold] = useState(1);
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

        const normalizedThreshold = Math.max(1, Math.floor(Number(activationThreshold) || 1));

        setIsSubmitting(true);
        try {
            const response = await fetch(`${apiUrl}/vaults`, {
                method: "POST",
                headers: buildAuthHeaders(authToken, true),
                body: JSON.stringify({
                    name: normalizedName,
                    owner_message: ownerMessage.trim() || null,
                    grace_period_value: Number(gracePeriodValue),
                    grace_period_unit: gracePeriodUnit,
                    recipients: [],
                    activation_threshold: normalizedThreshold,
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
            setOwnerMessage("");
            setGracePeriodValue(30);
            setGracePeriodUnit("days");
            setActivationThreshold(1);
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
                Define a secure vault, set the grace period, and choose how many recipients must
                request activation before the grace-period timer starts.
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
                    label={`Grace Period (${gracePeriodUnit})`}
                    type="number"
                    min={1}
                    max={gracePeriodUnit === "days" ? 3650 : 87600}
                    value={gracePeriodValue}
                    onChange={(event) => setGracePeriodValue(Number(event.target.value))}
                    required
                />

                <label
                    htmlFor="grace-period-unit"
                    style={{
                        display: "flex",
                        flexDirection: "column",
                        gap: t.space.xs,
                    }}
                >
                    <Text variant="label" color="secondary">Grace Period Unit</Text>
                    <select
                        id="grace-period-unit"
                        value={gracePeriodUnit}
                        onChange={(event) => setGracePeriodUnit(event.target.value as "days" | "hours")}
                        style={{
                            width: "100%",
                            border: `1px solid ${t.colors.components.input.border}`,
                            backgroundColor: t.colors.components.input.bg,
                            color: t.colors.text.primary,
                            borderRadius: t.radius.full,
                            padding: `${t.space.s}px ${t.space.m}px`,
                            fontFamily: "var(--font-geist-sans), sans-serif",
                            fontSize: t.typography.body.fontSize,
                        }}
                    >
                        <option value="days">Days</option>
                        <option value="hours">Hours</option>
                    </select>
                </label>

                <Input
                    id="owner-message"
                    label="Message For Recipients"
                    value={ownerMessage}
                    onChange={(event) => setOwnerMessage(event.target.value)}
                    placeholder="Write the message that should appear on the delivery cover page."
                    multiline
                />

                <Input
                    id="activation-threshold"
                    label="Activation Threshold (recipient votes)"
                    type="number"
                    min={1}
                    max={100}
                    value={activationThreshold}
                    onChange={(event) => setActivationThreshold(Number(event.target.value))}
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
