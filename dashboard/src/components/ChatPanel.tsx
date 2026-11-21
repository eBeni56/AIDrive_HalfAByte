// Placeholder for the conversation with the voice assistant (speech to text, AI answer and text to speech
// are built separately). Messages come in through src/chat.ts.
import { useEffect, useRef } from "react";
import { useChatMessages } from "../chat";

export function ChatPanel() {
  const messages = useChatMessages();
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  return (
    <section className="panel chat">
      <div className="panel-title">
        <span>Assistant</span>
      </div>
      <div className="chat-list">
        {messages.length === 0 && <div className="chat-empty">The conversation will appear here.</div>}
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            <span className="who">{m.role === "assistant" ? "AI" : "Driver"}</span>
            {m.text}
          </div>
        ))}
        <div ref={end} />
      </div>
    </section>
  );
}
