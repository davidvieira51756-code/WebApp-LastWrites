"use client";

import type { FormEvent } from "react";
import { useMemo, useState } from "react";

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
    onCreated: (vault: Vault) => void;
};

export default function CreateVaultForm({ apiUrl, onCreated }: CreateVaultFormProps) {
    const [name, setName] = useState("");
    const [gracePeriodDays, setGracePeriodDays] = useState(30);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [successMessage, setSuccessMessage] = useState<string | null>(null);

    const defaultUserId = useMemo(
        () => process.env.NEXT_PUBLIC_DEFAULT_USER_ID?.trim() || "academic-demo-user",
        []
    );

    const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        setErrorMessage(null);
        setSuccessMessage(null);

        if (!apiUrl) {
            setErrorMessage("NEXT_PUBLIC_API_URL is not configured.");
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
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    user_id: defaultUserId,
                    name: normalizedName,
                    grace_period_days: Number(gracePeriodDays),
                    status: "active",
                    recipients: [],
                }),
            });

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
        <section className="rounded-2xl border border-slate-200 bg-white/85 p-6 shadow-xl shadow-slate-200/70 backdrop-blur">
            <h2 className="font-['Fraunces',serif] text-2xl font-semibold text-slate-900">
                Create New Vault
            </h2>
            <p className="mt-2 text-sm text-slate-600">
                Define a secure vault and set the grace period for delivery activation.
            </p>

            <form onSubmit={handleSubmit} className="mt-6 space-y-4">
                <div>
                    <label htmlFor="vault-name" className="mb-2 block text-sm font-medium text-slate-700">
                        Vault Name
                    </label>
                    <input
                        id="vault-name"
                        type="text"
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        placeholder="Family Legacy Vault"
                        className="w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-slate-900 outline-none ring-0 transition focus:border-teal-500 focus:shadow-[0_0_0_4px_rgba(20,184,166,0.15)]"
                        required
                    />
                </div>

                <div>
                    <label
                        htmlFor="grace-period"
                        className="mb-2 block text-sm font-medium text-slate-700"
                    >
                        Grace Period (days)
                    </label>
                    <input
                        id="grace-period"
                        type="number"
                        min={1}
                        max={3650}
                        value={gracePeriodDays}
                        onChange={(event) => setGracePeriodDays(Number(event.target.value))}
                        className="w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-slate-900 outline-none ring-0 transition focus:border-teal-500 focus:shadow-[0_0_0_4px_rgba(20,184,166,0.15)]"
                        required
                    />
                </div>

                <button
                    type="submit"
                    disabled={isSubmitting}
                    className="w-full rounded-xl bg-gradient-to-r from-teal-600 to-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:from-teal-500 hover:to-cyan-500 disabled:cursor-not-allowed disabled:opacity-60"
                >
                    {isSubmitting ? "Creating Vault..." : "Create Vault"}
                </button>
            </form>

            {errorMessage ? (
                <p className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                    {errorMessage}
                </p>
            ) : null}

            {successMessage ? (
                <p className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                    {successMessage}
                </p>
            ) : null}
        </section>
    );
}
