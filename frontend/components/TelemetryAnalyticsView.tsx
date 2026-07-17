"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { BarChart3, Check, Copy, Link2, Network, Search, WalletCards } from "lucide-react";
import { supabase } from "@/lib/supabase";

type IndicatorType = "network_domain" | "canonical_url" | "wallet_address";
type IndicatorFilter = "all" | IndicatorType;

type TelemetryIndicator = {
    id: string;
    credential_id: string | null;
    telegram_msg_id?: number | null;
    message_id?: string | null;
    indicator_type: IndicatorType;
    indicator_value: string;
    first_seen_at: string | null;
    meta?: Record<string, unknown> | null;
    raw_context?: Record<string, unknown> | null;
};

const INDICATOR_TYPES: IndicatorType[] = ["network_domain", "canonical_url", "wallet_address"];

const TYPE_LABELS: Record<IndicatorType, string> = {
    network_domain: "Network Domain",
    canonical_url: "Canonical URL",
    wallet_address: "Blockchain Wallet",
};

const TYPE_STYLES: Record<IndicatorType, string> = {
    network_domain: "bg-cyan-50 text-cyan-700 border-cyan-100",
    canonical_url: "bg-indigo-50 text-indigo-700 border-indigo-100",
    wallet_address: "bg-emerald-50 text-emerald-700 border-emerald-100",
};

export default function TelemetryAnalyticsView() {
    const [indicators, setIndicators] = useState<TelemetryIndicator[]>([]);
    const [counts, setCounts] = useState<Record<IndicatorType, number>>({
        network_domain: 0,
        canonical_url: 0,
        wallet_address: 0,
    });
    const [filter, setFilter] = useState<IndicatorFilter>("all");
    const [query, setQuery] = useState("");
    const [copiedId, setCopiedId] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        async function fetchTelemetry() {
            setIsLoading(true);
            setError(null);

            const [recordsResult, ...countResults] = await Promise.all([
                supabase
                    .from("telemetry_indicators")
                    .select("*")
                    .order("first_seen_at", { ascending: false })
                    .limit(100),
                ...INDICATOR_TYPES.map((indicatorType) =>
                    supabase
                        .from("telemetry_indicators")
                        .select("id", { count: "exact", head: true })
                        .eq("indicator_type", indicatorType)
                ),
            ]);

            if (recordsResult.error) {
                setError(recordsResult.error.message);
                setIndicators([]);
                setIsLoading(false);
                return;
            }

            const nextIndicators = (recordsResult.data || []) as TelemetryIndicator[];
            const nextCounts: Record<IndicatorType, number> = {
                network_domain: 0,
                canonical_url: 0,
                wallet_address: 0,
            };
            INDICATOR_TYPES.forEach((indicatorType, index) => {
                nextCounts[indicatorType] =
                    countResults[index].count ??
                    nextIndicators.filter((row) => row.indicator_type === indicatorType).length;
            });

            setIndicators(nextIndicators);
            setCounts(nextCounts);
            setIsLoading(false);
        }

        fetchTelemetry();
    }, []);

    const filteredIndicators = useMemo(() => {
        const normalizedQuery = query.trim().toLowerCase();
        return indicators.filter((indicator) => {
            const matchesType = filter === "all" || indicator.indicator_type === filter;
            const matchesQuery =
                !normalizedQuery ||
                indicator.indicator_value.toLowerCase().includes(normalizedQuery) ||
                indicator.indicator_type.toLowerCase().includes(normalizedQuery);
            return matchesType && matchesQuery;
        });
    }, [filter, indicators, query]);

    async function copyIndicator(indicator: TelemetryIndicator) {
        await navigator.clipboard.writeText(indicator.indicator_value);
        setCopiedId(indicator.id);
        window.setTimeout(() => setCopiedId(null), 1200);
    }

    return (
        <section className="flex-1 overflow-y-auto bg-slate-950 text-slate-100">
            <div className="border-b border-white/10 bg-slate-900/90 px-6 py-4">
                <div className="flex items-center justify-between gap-4">
                    <div>
                        <h1 className="flex items-center gap-2 text-lg font-semibold">
                            <BarChart3 className="h-5 w-5 text-cyan-300" />
                            Telemetry Analytics
                        </h1>
                        <p className="mt-1 text-sm text-slate-400">
                            Canonical endpoint and extracted entity index across the ingestion fabric.
                        </p>
                    </div>
                    <span className="rounded border border-white/10 bg-white/5 px-2 py-1 text-xs font-mono text-slate-300">
                        latest 100 rows
                    </span>
                </div>
            </div>

            <div className="space-y-4 p-6">
                <div className="grid grid-cols-3 gap-3">
                    <MetricCard
                        icon={<Network className="h-4 w-4" />}
                        label="Total Network Domains"
                        value={counts.network_domain}
                    />
                    <MetricCard
                        icon={<Link2 className="h-4 w-4" />}
                        label="Total Canonical URLs"
                        value={counts.canonical_url}
                    />
                    <MetricCard
                        icon={<WalletCards className="h-4 w-4" />}
                        label="Total Blockchain Wallets"
                        value={counts.wallet_address}
                    />
                </div>

                <div className="rounded border border-white/10 bg-white/[0.04]">
                    <div className="flex items-center gap-3 border-b border-white/10 p-3">
                        <div className="relative min-w-0 flex-1">
                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                            <input
                                value={query}
                                onChange={(event) => setQuery(event.currentTarget.value)}
                                placeholder="Search indicator values"
                                className="h-9 w-full rounded border border-white/10 bg-slate-950 pl-9 pr-3 text-sm text-slate-100 outline-none transition-colors placeholder:text-slate-500 focus:border-cyan-400"
                            />
                        </div>
                        <div className="flex rounded bg-slate-950 p-1">
                            {(["all", ...INDICATOR_TYPES] as IndicatorFilter[]).map((type) => (
                                <button
                                    key={type}
                                    type="button"
                                    onClick={() => setFilter(type)}
                                    className={`rounded px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                                        filter === type
                                            ? "bg-cyan-400 text-slate-950"
                                            : "text-slate-400 hover:text-slate-100"
                                    }`}
                                >
                                    {type === "all" ? "All" : TYPE_LABELS[type]}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="overflow-x-auto">
                        <table className="w-full table-fixed text-left text-sm">
                            <thead className="bg-slate-900 text-xs uppercase text-slate-500">
                                <tr>
                                    <th className="w-44 px-4 py-3">Type</th>
                                    <th className="px-4 py-3">Indicator Value</th>
                                    <th className="w-48 px-4 py-3">First Seen</th>
                                    <th className="w-20 px-4 py-3 text-right">Copy</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-white/10">
                                {isLoading && (
                                    <tr>
                                        <td className="px-4 py-8 text-center text-slate-400" colSpan={4}>
                                            Loading telemetry indicators...
                                        </td>
                                    </tr>
                                )}
                                {!isLoading && error && (
                                    <tr>
                                        <td className="px-4 py-8 text-center text-rose-300" colSpan={4}>
                                            {error}
                                        </td>
                                    </tr>
                                )}
                                {!isLoading && !error && filteredIndicators.length === 0 && (
                                    <tr>
                                        <td className="px-4 py-8 text-center text-slate-400" colSpan={4}>
                                            No indicators match the current filter.
                                        </td>
                                    </tr>
                                )}
                                {!isLoading && !error && filteredIndicators.map((indicator) => (
                                    <tr key={indicator.id} className="hover:bg-white/[0.03]">
                                        <td className="px-4 py-3">
                                            <span className={`inline-flex rounded border px-2 py-1 text-xs font-semibold ${TYPE_STYLES[indicator.indicator_type]}`}>
                                                {TYPE_LABELS[indicator.indicator_type]}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className="block truncate font-mono text-xs text-slate-200" title={indicator.indicator_value}>
                                                {indicator.indicator_value}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-xs text-slate-400">
                                            {indicator.first_seen_at
                                                ? new Date(indicator.first_seen_at).toLocaleString()
                                                : "Unknown"}
                                        </td>
                                        <td className="px-4 py-3 text-right">
                                            <button
                                                type="button"
                                                onClick={() => copyIndicator(indicator)}
                                                className="inline-flex h-8 w-8 items-center justify-center rounded border border-white/10 text-slate-300 transition-colors hover:border-cyan-300 hover:text-cyan-200"
                                                title="Copy indicator value"
                                                aria-label="Copy indicator value"
                                            >
                                                {copiedId === indicator.id ? (
                                                    <Check className="h-4 w-4" />
                                                ) : (
                                                    <Copy className="h-4 w-4" />
                                                )}
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </section>
    );
}

function MetricCard({
    icon,
    label,
    value,
}: {
    icon: ReactNode;
    label: string;
    value: number;
}) {
    return (
        <div className="rounded border border-white/10 bg-white/[0.05] p-4">
            <div className="flex items-center justify-between text-slate-400">
                <span className="text-xs font-semibold uppercase tracking-wide">{label}</span>
                {icon}
            </div>
            <div className="mt-3 font-mono text-3xl font-semibold text-white">
                {value.toLocaleString()}
            </div>
        </div>
    );
}
