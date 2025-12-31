"use client";

import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";
import ChatWindow from "@/components/ChatWindow";
import { LucideTarget, LucideSmartphone } from "lucide-react";

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
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
      <Sidebar selectedId={selectedId} onSelect={setSelectedId} />
      <ChatWindow credentialId={selectedId || ""} />
    </main>
  );
}
