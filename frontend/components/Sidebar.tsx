"use client";

import { useEffect, useState, useRef } from "react";
import { supabase } from "@/lib/supabase";
import { LucideTarget } from "lucide-react";

interface Credential {
    id: string;
    created_at: string;
    source: string;
    meta?: {
        chat_title?: string;
        bot_username?: string;
        bot_id?: string;
        [key: string]: unknown;
    };
}

interface MessageWithCredential {
    credential_id: string;
    discovered_credentials: Credential | null;
}

export default function Sidebar({
    selectedId,
    onSelect,
}: {
    selectedId: string | null;
    onSelect: (id: string) => void;
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

            // Query exfiltrated_messages and group by credential_id
            // This gets us unique credentials that have messages
            // NOTE: We deliberately exclude bot_token for security
            const { data, error } = await supabase
                .from("exfiltrated_messages")
                .select("credential_id, discovered_credentials(id, created_at, source, meta)")
                .order("created_at", { ascending: false });

            if (error) {
                console.error("[Sidebar] Error fetching messages:", error.message);
                return;
            }

            console.log(`[Sidebar] Received ${data?.length || 0} message records`);

            if (data) {
                // Group by credential_id to get unique credentials
                const uniqueCredMap = new Map<string, Credential>();

                // Cast data since supabase return type inference might vary
                const messages = data as unknown as MessageWithCredential[];

                messages.forEach((msg) => {
                    const credId = msg.credential_id;
                    // Supabase returns joined data as a single object for Many-to-One
                    const credInfo = msg.discovered_credentials;

                    if (credInfo && !uniqueCredMap.has(credId)) {
                        uniqueCredMap.set(credId, {
                            id: credInfo.id,
                            created_at: credInfo.created_at,
                            source: credInfo.source,
                            meta: credInfo.meta
                        });
                    }
                });

                // Convert map to array and sort by created_at desc
                const uniqueCreds = Array.from(uniqueCredMap.values())
                    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

                console.log(`[Sidebar] Found ${uniqueCreds.length} unique bots (sources: ${[...new Set(uniqueCreds.map(c => c.source))].join(', ') || 'none'})`);
                setCredentials(uniqueCreds);
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
                        // Fetch the credential details
                        const { data: credData } = await supabase
                            .from("discovered_credentials")
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
                        onClick={() => onSelect(cred.id)}
                        className={`p-4 border-b text-left hover:bg-slate-100 transition-colors ${selectedId === cred.id ? "bg-blue-50 border-l-4 border-l-blue-500" : ""
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
