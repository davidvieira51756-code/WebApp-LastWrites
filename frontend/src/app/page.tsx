"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import CreateVaultForm, { type Vault } from "../components/CreateVaultForm";

export default function DashboardPage() {
    const apiUrl = useMemo(
        () => (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, ""),
        []
    );

    const [vaults, setVaults] = useState<Vault[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    const fetchVaults = useCallback(async () => {
        if (!apiUrl) {
            setErrorMessage("NEXT_PUBLIC_API_URL is not configured.");
            setVaults([]);
            return;
        }

        setIsLoading(true);
        setErrorMessage(null);
        try {
            const response = await fetch(`${apiUrl}/vaults`, {
                method: "GET",
            });

            if (!response.ok) {
                let detail = "Failed to fetch vaults.";
                try {
                    const errorPayload = (await response.json()) as { detail?: string };
                    if (errorPayload.detail) {
                        detail = errorPayload.detail;
                    }
                } catch {
                    detail = `Failed to fetch vaults. HTTP ${response.status}.`;
                }
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
    }, [apiUrl]);

    useEffect(() => {
        void fetchVaults();
    }, [fetchVaults]);

    const handleVaultCreated = useCallback((createdVault: Vault) => {
        setVaults((previousVaults) => [
            createdVault,
            ...previousVaults.filter((vault) => vault.id !== createdVault.id),
        ]);
    }, []);

    return (
        <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(20,184,166,0.18),_transparent_40%),radial-gradient(circle_at_85%_15%,_rgba(14,165,233,0.15),_transparent_35%),linear-gradient(180deg,_#f8fafc_0%,_#ecfeff_45%,_#f8fafc_100%)] px-4 py-10 font-['Space_Grotesk',sans-serif] text-slate-900 sm:px-8 lg:px-12">
            <div className="mx-auto w-full max-w-6xl">
                <header className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <p className="inline-block rounded-full border border-cyan-200 bg-cyan-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">
                            Last Writes
                        </p>
                        <h1 className="mt-3 font-['Fraunces',serif] text-4xl font-semibold leading-tight text-slate-900 sm:text-5xl">
                            Vault Dashboard
                        </h1>
                        <p className="mt-2 max-w-2xl text-sm text-slate-600 sm:text-base">
                            Manage digital legacy vaults, monitor grace periods, and keep recipient delivery
                            rules up to date.
                        </p>
                    </div>

                    <button
                        type="button"
                        onClick={() => void fetchVaults()}
                        className="rounded-xl border border-slate-300 bg-white/90 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-cyan-400 hover:text-cyan-700"
                    >
                        Refresh Vaults
                    </button>
                </header>

                {!apiUrl ? (
                    <div className="mb-6 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                        Configure NEXT_PUBLIC_API_URL in your frontend environment to connect to FastAPI.
                    </div>
                ) : null}

                <section className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(320px,380px)_minmax(0,1fr)]">
                    <CreateVaultForm apiUrl={apiUrl} onCreated={handleVaultCreated} />

                    <div className="rounded-2xl border border-slate-200 bg-white/85 p-6 shadow-xl shadow-slate-200/70 backdrop-blur">
                        <div className="mb-5 flex items-center justify-between">
                            <h2 className="font-['Fraunces',serif] text-2xl font-semibold text-slate-900">
                                Existing Vaults
                            </h2>
                            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
                                {vaults.length} total
                            </span>
                        </div>

                        {isLoading ? (
                            <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                                Loading vaults...
                            </p>
                        ) : null}

                        {errorMessage ? (
                            <p className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                                {errorMessage}
                            </p>
                        ) : null}

                        {!isLoading && !errorMessage && vaults.length === 0 ? (
                            <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                                No vaults found.
                            </p>
                        ) : null}

                        <div className="space-y-3">
                            {vaults.map((vault) => (
                                <article
                                    key={vault.id}
                                    className="rounded-xl border border-slate-200 bg-white p-4 transition hover:border-cyan-300"
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <h3 className="text-base font-semibold text-slate-900">{vault.name}</h3>
                                            <p className="mt-1 text-xs uppercase tracking-wide text-slate-500">
                                                Vault ID: {vault.id}
                                            </p>
                                        </div>
                                        <span className="rounded-full bg-teal-50 px-2.5 py-1 text-xs font-semibold text-teal-700">
                                            {vault.status}
                                        </span>
                                    </div>

                                    <div className="mt-3 grid grid-cols-2 gap-3 text-sm text-slate-600">
                                        <p>
                                            Grace Period: <span className="font-medium text-slate-800">{vault.grace_period_days} days</span>
                                        </p>
                                        <p>
                                            Recipients: <span className="font-medium text-slate-800">{vault.recipients.length}</span>
                                        </p>
                                    </div>
                                </article>
                            ))}
                        </div>
                    </div>
                </section>
            </div>
        </main>
    );
}
