import React, { useCallback, useEffect, useRef, useState } from "react";
import Scene from "./Scene.jsx";
import Transcript from "./Transcript.jsx";
import { api } from "../lib/api.js";
import { canListen, listen, say, stopSpeaking } from "../lib/speech.js";

/**
 * The daily episode.
 *
 * One exchange is two requests, and the scene plays them in order rather than
 * waiting for both. The learner's line appears and is spoken while the other
 * character's answer is still being generated, so the conversation never stops
 * to wait — which is what "out of sync" meant. `busy` gates input; `speaker`
 * and `phase` are what the animation reads.
 */
export default function Story({ onWord, autoVoice }) {
  const [day, setDay] = useState(null);
  const [turns, setTurns] = useState([]);
  const [speaker, setSpeaker] = useState(null);
  const [phase, setPhase] = useState("approach");
  const [subtitle, setSubtitle] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [script, setScript] = useState(false);
  // The other character's line, fetched but deliberately not played.
  //
  // Auto-playing it meant the reply arrived while the learner was still reading
  // his own sentence, so he skimmed both. Holding it behind a button separates
  // "I said this" from "they said that" into two acts of attention, which is
  // the whole reason the exchange is worth reading twice.
  const [pending, setPending] = useState(null);
  const recognitionRef = useRef(null);
  const inputRef = useRef(null);

  const play = useCallback(async (turn, who) => {
    setSpeaker(who);
    setPhase("talk");
    setSubtitle(who === "npc" ? turn.en || "" : turn.learner_input || turn.en || "");
    if (autoVoice) await say(turn.de, { rate: 0.9, pitch: who === "npc" ? 1.08 : 0.95 });
    else await new Promise((r) => setTimeout(r, Math.min(4200, 900 + turn.de.length * 45)));
  }, [autoVoice]);

  // Space or Enter also advances, so the flow does not require the mouse.
  useEffect(() => {
    const onKey = (event) => {
      if (!pending) return;
      if (event.target.tagName === "INPUT" && event.key !== "Enter") return;
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); advance(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    let live = true;
    api.story().then(async (data) => {
      if (!live) return;
      setDay(data);
      setTurns(data.turns);
      setPhase("approach");
      await new Promise((r) => setTimeout(r, 2100));
      if (!live) return;
      setPhase("meet");
      const last = data.turns[data.turns.length - 1];
      if (last) await play(last, last.speaker);
      if (live) setPhase("idle");
    }).catch(() => {});
    return () => { live = false; stopSpeaking(); };
  }, [play]);

  const send = async (text) => {
    const said = (text ?? draft).trim();
    if (!said || busy) return;
    setBusy(true);
    setDraft("");
    try {
      const mine = await api.say({ text: said, day: day?.day_number });
      setTurns((prev) => [...prev, mine.turn]);
      setDay((prev) => ({ ...prev, turns_used: mine.turns_used }));
      // Fetch their answer immediately so the wait is spent, not felt — but
      // hold it until he asks for it.
      const incoming = api.reply({ day: day?.day_number });
      await play(mine.turn, "learner");
      setPending(await incoming);
    } catch {
      setSubtitle("Connection lost — is the server running?");
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  const advance = async () => {
    if (!pending) return;
    const theirs = pending;
    setPending(null);
    setTurns((prev) => [...prev, theirs.turn]);
    setDay((prev) => ({ ...prev, complete: theirs.complete }));
    await play(theirs.turn, "npc");
    setPhase("idle");
    setSpeaker(null);
    inputRef.current?.focus();
  };

  const toggleMic = () => {
    if (listening) { recognitionRef.current?.stop(); return; }
    recognitionRef.current = listen({
      onPartial: setDraft,
      onFinal: (text) => { setDraft(text); },
      onEnd: () => setListening(false),
      onError: () => setListening(false),
    });
    if (recognitionRef.current) setListening(true);
  };

  const progress = day ? Math.min(100, (day.turns_used / day.turns_target) * 100) : 0;

  return (
    <div className="story">
      <div className="stagewrap">
        <Scene day={day} turns={turns} speaker={speaker} phase={phase}
               subtitle={subtitle} onWord={onWord} waiting={Boolean(pending)}
               npcName={day?.npc} onAdvance={advance} />
        {!script ? (
          <button type="button" className="scriptbtn"
                  onClick={(e) => { e.stopPropagation(); setScript(true); }}>
            📜 Gespräch <b>{turns.length}</b>
          </button>
        ) : null}
        <Transcript turns={turns} day={day} open={script} onWord={onWord}
                    onClose={() => setScript(false)} />
      </div>

      <div className="composer">
        <div className="composer__meta">
          <div className="composer__bar"><i style={{ width: `${progress}%` }} /></div>
          <span>{day?.turns_used ?? 0} / {day?.turns_target ?? 50} Runden</span>
        </div>
        <div className="composer__row">
          {canListen() ? (
            <button type="button" className={`mic ${listening ? "is-live" : ""}`}
                    onClick={toggleMic} title="Auf Deutsch sprechen">🎤</button>
          ) : null}
          <input
            ref={inputRef}
            value={draft}
            disabled={busy || Boolean(pending)}
            placeholder={busy ? "…" : "Sag etwas — Englisch oder Deutsch"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          />
          {pending ? (
            <button type="button" className="btn btn--next" onClick={advance}>
              {day?.npc?.split(" ")[0] ?? "Sie"} antwortet →
            </button>
          ) : (
            <button type="button" className="btn" disabled={busy || !draft.trim()}
                    onClick={() => send()}>Sagen</button>
          )}
        </div>
        <p className="composer__hint">
          {pending
            ? <>Read your line, then press <b>Enter</b> to hear the reply</>
            : <>Type <b>English</b> and your character says it in German ·
               type <b>German</b> and it gets corrected</>}
        </p>
      </div>
    </div>
  );
}
