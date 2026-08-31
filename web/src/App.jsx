import React, { useCallback, useEffect, useState } from "react";
import Story from "./components/Story.jsx";
import WordCard from "./components/WordCard.jsx";
import Vokabeln from "./components/Vokabeln.jsx";
import Grammatik from "./components/Grammatik.jsx";
import { Hoeren, Lesen, Schreiben, Sprechen } from "./components/Exam.jsx";
import { api } from "./lib/api.js";

// "Jana Instructor" is gone. It was a second conversational surface competing
// with the story for the same job, and two chat interfaces in one product is a
// sign the product has not decided what it is. The story is the conversation.
const TABS = [
  { id: "story", label: "Die Geschichte", icon: "🎬", full: true },
  { id: "drill", label: "Vokabeln", icon: "🗂️" },
  { id: "grammatik", label: "Grammatik", icon: "🧩", full: true },
  { id: "lesen", label: "Lesen", icon: "📖" },
  { id: "hoeren", label: "Hören", icon: "🎧" },
  { id: "schreiben", label: "Schreiben", icon: "✍️" },
  { id: "sprechen", label: "Sprechen", icon: "🎤" },
];

export default function App() {
  const [tab, setTab] = useState("story");
  const [word, setWord] = useState(null);
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState(null);
  const [autoVoice, setAutoVoice] = useState(true);
  // The day every practice module anchors to. Read once here rather than in
  // each module, so all five are looking at the same episode.
  const [day, setDay] = useState(null);

  const refresh = useCallback(() => {
    api.state().then((s) => setStats(s.stats)).catch(() => {});
    api.health().then(setHealth).catch(() => {});
  }, []);

  useEffect(() => {
    api.story().then((d) => setDay(d.day_number)).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 25000);
    return () => clearInterval(timer);
  }, [refresh]);

  const days = stats
    ? Math.ceil((new Date(`${stats.exam_date}T09:00:00`) - Date.now()) / 864e5)
    : null;
  const active = TABS.find((t) => t.id === tab);

  return (
    <div className="app" onClick={() => setWord(null)}>
      <header className="topbar">
        <span className="brand">Jana</span>
        <span className="tagline">Hyderabad → Hamburg 🇮🇳→🇩🇪</span>
        <div className="topbar__right">
          <button type="button" className="btn btn--ghost"
                  style={{ padding: "5px 12px", fontSize: 12 }}
                  onClick={(e) => { e.stopPropagation(); setAutoVoice((v) => !v); }}>
            🔊 Stimme: {autoVoice ? "AN" : "AUS"}
          </button>
          {health ? (
            <span>
              <span className={`dot ${health.ollama ? "dot--on" : "dot--off"}`} />lokal
              <span style={{ marginLeft: 12 }} />
              <span className={`dot ${health.deepseek ? "dot--on" : "dot--off"}`} />deepseek
            </span>
          ) : null}
          {stats ? <span>fällig <b>{stats.due}</b></span> : null}
          {stats ? <span>Antworten <b>{stats.attempts?.toLocaleString()}</b></span> : null}
          {days !== null ? <span><b>{days}</b> Tage bis B1</span> : null}
        </div>
      </header>

      <nav className="tabs">
        {TABS.map((item) => (
          <button key={item.id} type="button"
                  className={item.id === tab ? "is-active" : ""}
                  onClick={(e) => { e.stopPropagation(); setTab(item.id); }}>
            {item.icon} {item.label}
          </button>
        ))}
      </nav>

      <main className={`view ${active?.full ? "view--full" : ""}`}>
        {tab === "story" ? <Story onWord={setWord} autoVoice={autoVoice} /> : null}
        {tab === "drill" ? <Vokabeln onWord={setWord} day={day} /> : null}
        {tab === "grammatik" ? <Grammatik onWord={setWord} day={day} /> : null}
        {tab === "lesen" ? <Lesen onWord={setWord} day={day} /> : null}
        {tab === "hoeren" ? <Hoeren onWord={setWord} day={day} /> : null}
        {tab === "schreiben" ? <Schreiben onWord={setWord} day={day} /> : null}
        {tab === "sprechen" ? <Sprechen onWord={setWord} day={day} /> : null}
      </main>

      {word ? <WordCard word={word} onClose={() => setWord(null)} /> : null}
    </div>
  );
}
