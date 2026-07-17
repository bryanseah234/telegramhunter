"use client";

import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";
import ChatWindow from "@/components/ChatWindow";
import TelemetryAnalyticsView from "@/components/TelemetryAnalyticsView";
import { LucideTarget, LucideSmartphone } from "lucide-react";

export type DashboardView = "chat" | "telemetry";

export type GatewayTelemetry = {
  configured_webhook_url?: string | null;
  resolved_ip_address?: string | null;
  command_dictionary?: Array<{ command?: string | null; description?: string | null }>;
  error_profile?: unknown;
  last_error_info?: string | null;
};

export type InfrastructureContext = {
  source_file_path?: string | null;
  repository_uri?: string | null;
  co_located_endpoints?: string[];
  [key: string]: unknown;
};

export interface Credential {
  id: string;
  created_at: string;
  source: string;
  // Bundle 4: surfaced as top-level int columns from the DB view (generated columns)
  confidence_score?: number | null;
  chat_member_count?: number | null;
  meta?: {
    chat_title?: string;
    bot_username?: string;
    bot_id?: string;
    gateway_telemetry?: GatewayTelemetry;
    infrastructure_context?: InfrastructureContext;
    [key: string]: unknown;
  };
}

export default function Home() {
  const [selected, setSelected] = useState<Credential | null>(null);
  const [activeView, setActiveView] = useState<DashboardView>("chat");
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  if (isMobile) {
    return (
      <main className="flex h-screen w-full flex-col items-center justify-center bg-gradient-to-br from-slate-900 to-slate-800 p-8 text-center">
        <LucideTarget className="w-20 h-20 text-red-500 mb-6" />
        <h1 className="text-2xl font-bold text-white mb-2">
          Mobile Not Supported
        </h1>
        <p className="text-slate-400 mb-8 max-w-sm">
          This dashboard is designed for desktop viewing. To see live chats, join our Telegram channel instead!
        </p>
        <a
          href="https://t.me/theprawnhunter"
          className="inline-flex items-center gap-2 bg-sky-500 hover:bg-sky-600 text-white font-semibold py-3 px-6 rounded-full transition-colors shadow-lg"
        >
          <LucideSmartphone className="w-5 h-5" />
          Open in Telegram
        </a>
        <p className="text-slate-500 text-xs mt-4">
          t.me/theprawnhunter
        </p>
      </main>
    );
  }

  return (
    <main className="flex h-screen w-full overflow-hidden bg-white">
      <Sidebar
        selected={selected}
        activeView={activeView}
        onViewChange={setActiveView}
        onSelect={(cred) => {
          setSelected(cred);
          setActiveView("chat");
        }}
      />
      {activeView === "telemetry" ? (
        <TelemetryAnalyticsView />
      ) : (
        <ChatWindow credential={selected} />
      )}
    </main>
  );
}
