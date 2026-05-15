"use client";

import { useEffect, useState, useRef } from "react";
import { supabase } from "@/lib/supabase";
import { LucideTarget } from "lucide-react";
import type { Credential } from "@/app/page";

export default function Sidebar({
    selected,
    onSelect,
}: {
    selected: Credential | null;
    onSelect: (cred: Credential) => void;
}) {
    const [credentials, setCredentials] = useState<Credential[]>([]);
    // Use ref to access current credentials in realtime callback without causing re-subscription
    const credentialsRef = useRef<Credential[]>([]);

    // Keep ref in sync with state
    useEffect(() => {
        credentialsRef.current = credentials;
    }, [credentials]);

    useEffect(() => {
        async function fetchCreds() {
            console.log("[Sidebar] Fetching credentials...");

            try {
                // Fetch credentials that have messages directly — avoids the 10 000-row
                // message scan previously used to derive credential IDs.
                const { data: creds, error } = await supabase
                    .from("discovered_credentials_public")
                    .select("id, created_at, source, meta")
                    .not("id", "is", null)
                    .limit(500);
                // Supabase RLS on discovered_credentials_public already filters to
                // credentials the anon key is allowed to see.

                if (error) {
                    console.error("[Sidebar] Error fetching credentials:", error.message);
                    return;
                }

                const sorted = (creds || [])
                    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

                console.log(`[Sidebar] Found ${sorted.length} bots with messages (sources: ${[...new Set(sorted.map(c => c.source))].join(', ') || 'none'})`);
                setCredentials(sorted);
            } catch (err) {
                console.error("[Sidebar] Exception fetching creds:", err);
            }
        }

        fetchCreds();

        // Realtime subscription - when new message arrives, check if it's a new credential
        const channel = supabase
            .channel('schema-db-changes')
            .on(
                'postgres_changes',
                {
                    event: 'INSERT',
                    schema: 'public',
                    table: 'exfiltrated_messages',
                },
                async (payload) => {
                    const newMsg = payload.new as { credential_id: string };
                    const credId = newMsg.credential_id;

                    // Use ref to check current credentials without causing re-subscription
                    const exists = credentialsRef.current.some(c => c.id === credId);

                    if (!exists) {
                        // Fetch via the safe public view — anon key cannot SELECT raw table
                        const { data: credData } = await supabase
                            .from("discovered_credentials_public")
                            .select("*")
                            .eq("id", credId)
                            .single();

                        if (credData) {
                            setCredentials((prev) => [credData, ...prev]);
                        }
                    }
                }
            )
            .subscribe()

        return () => {
            supabase.removeChannel(channel);
        }
    }, []); // ✅ Empty dependency array - runs once on mount

    return (
        <div className="w-1/3 min-w-75 shrink-0 border-r h-full flex flex-col bg-slate-50 overflow-y-auto">
            <div className="p-4 border-b bg-white sticky top-0 z-10">
                <h2 className="font-bold text-lg flex items-center gap-2 text-slate-800">
                    <LucideTarget className="text-red-600" /> Discovered Bots
                </h2>
            </div>
            <div className="flex flex-col">
                {credentials.map((cred) => (
                    <button
                        key={cred.id}
                        onClick={() => onSelect(cred)}
                        className={`p-4 border-b text-left hover:bg-slate-100 transition-colors ${selected?.id === cred.id ? "bg-blue-50 border-l-4 border-l-blue-500" : ""
                            }`}
                    >
                        <div className="flex justify-between w-full mb-1">
                            <span className="font-semibold text-slate-800 truncate">
                                {cred.meta?.bot_username
                                    ? `@${cred.meta.bot_username} / ${cred.meta.bot_id || '?'}`
                                    : (cred.meta?.bot_id ? `@unknown / ${cred.meta.bot_id}` : (cred.meta?.chat_title || "Unknown Chat"))}
                            </span>
                            <span className="text-xs text-slate-400">
                                {new Date(cred.created_at).toLocaleDateString()}
                            </span>
                        </div>
                        <div className="text-sm text-slate-500 truncate flex items-center gap-1">
                            <span className="bg-slate-200 px-1 py-0.5 rounded text-[10px] uppercase font-mono">{cred.source}</span>
                            <span className="font-mono text-xs opacity-70 truncate">ID: {cred.meta?.bot_id || cred.id.slice(0, 8)}</span>
                        </div>
                    </button>
                ))}
            </div>
        </div>
    );
}
