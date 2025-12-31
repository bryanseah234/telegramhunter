"use client";

import { useState } from "react";
import Sidebar from "./components/Sidebar";
import ChatWindow from "./components/ChatWindow";

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <main className="flex h-screen w-full overflow-hidden bg-white">
      <Sidebar selectedId={selectedId} onSelect={setSelectedId} />
      <ChatWindow credentialId={selectedId || ""} />
    </main>
  );
}
