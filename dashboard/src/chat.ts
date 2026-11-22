// The conversation shown in the chat panel. This is the only hook for the voice assistant:
//   - send {"type": "chat_message", "role": "user" | "assistant", "text": "..."} to the server, or
//   - call addChatMessage(role, text) from code in this page (also window.dashboardChat.add in the console).
import { useSyncExternalStore } from "react";
import type { ChatMessage } from "./types";

let messages: ChatMessage[] = [];
const listeners = new Set<() => void>();

export function addChatMessage(role: ChatMessage["role"], text: string): void {
  messages = [...messages, { role, text }];
  listeners.forEach((l) => l());
}

export function useChatMessages(): ChatMessage[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => messages,
  );
}

declare global {
  interface Window {
    dashboardChat: { add: typeof addChatMessage };
  }
}
window.dashboardChat = { add: addChatMessage };
