import React, { useEffect, useRef } from "react";
import GermanText from "./GermanText.jsx";
import { speak } from "../lib/speech.js";

/**
 * The whole conversation, held beside the scene.
 *
 * The stage can only show the last thing each side said — that is what a stage
 * is. But a conversation the learner cannot scroll back through is a
 * conversation he cannot study, and re-reading is where most of the retention
 * actually happens. So the transcript is the archive and the scene is the
 * performance; both are live, neither replaces the other.
 *
 * Every German word here is clickable, same as on the stage, because the moment
 * he wants a meaning is the moment he is re-reading — not the moment the line
 * was first spoken and the audio was still playing.
 */
export default function Transcript({ turns, day, open, onClose, onWord }) {
  const endRef = useRef(null);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, open]);

  if (!open) return null;

  return (
    <aside className="transcript" onClick={(event) => event.stopPropagation()}>
      <header className="transcript__head">
        <div>
          <span className="transcript__title">Tag {day?.day_number} · {day?.title}</span>
          <span className="transcript__count">{turns.length} Zeilen</span>
        </div>
        <button type="button" className="wordcard__close" onClick={onClose}>✕</button>
      </header>

      <div className="transcript__body">
        {turns.map((turn, index) => {
          const mine = turn.speaker === "learner";
          return (
            <article key={index} className={`entry ${mine ? "entry--me" : "entry--npc"}`}>
              <div className="entry__who">
                {mine ? "Du" : day?.npc}
                <button type="button" className="entry__play"
                        onClick={() => speak(turn.de, { rate: 0.85 })}
                        title="Anhören">🔊</button>
              </div>
              <GermanText text={turn.de} onWord={onWord} className="entry__de" />
              {turn.en ? <p className="entry__en">{turn.en}</p> : null}
              {mine && turn.input_lang === "en" && turn.learner_input ? (
                <p className="entry__said">you said: “{turn.learner_input}”</p>
              ) : null}
              {turn.correction ? <p className="entry__fix">✎ {turn.correction}</p> : null}
            </article>
          );
        })}
        <div ref={endRef} />
      </div>

      <footer className="transcript__foot">
        Tap any German word for its meaning
      </footer>
    </aside>
  );
}
