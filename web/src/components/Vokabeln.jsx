import React, { useCallback, useEffect, useState } from "react";
import GermanText from "./GermanText.jsx";
import { api } from "../lib/api.js";
import { glyphFor, genderClass } from "../lib/glyphs.js";
import { speak } from "../lib/speech.js";

/**
 * Flashcards, graded by the learner rather than by a machine.
 *
 * Two decisions worth naming.
 *
 * **The card is shown German-first and he grades his own recall.** Multiple
 * choice measures recognition, which is the easy half; FSRS wants to know how
 * hard the retrieval was, and only he knows that. The four buttons map to the
 * FSRS grades, so "Fast" and "Leicht" produce genuinely different intervals
 * rather than both meaning "correct".
 *
 * **The back of the card carries the sentence he met it in.** A card reading
 * only `die Postleitzahl — postal code` is a cue-free retrieval. The same card
 * showing what the border officer actually said gives the memory a hook, and he
 * was there when it was made.
 */
const GRADES = [
  { grade: 1, label: "Nochmal", hint: "no idea", tone: "bad" },
  { grade: 2, label: "Schwer", hint: "barely", tone: "warm" },
  { grade: 3, label: "Gut", hint: "got it", tone: "ok" },
  { grade: 4, label: "Leicht", hint: "instant", tone: "easy" },
];

export default function Vokabeln({ onWord, day }) {
  const [cards, setCards] = useState(null);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [done, setDone] = useState(0);

  useEffect(() => {
    api.flashcards(day, 20).then((data) => {
      setCards(data.cards); setIndex(0); setFlipped(false); setDone(0);
    }).catch(() => setCards([]));
  }, [day]);

  const card = cards?.[index];

  const grade = useCallback(async (value) => {
    if (!card) return;
    await api.grade({ item_id: card.item_id, grade: value }).catch(() => {});
    setDone((n) => n + 1);
    setFlipped(false);
    setIndex((i) => i + 1);
  }, [card]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.target.tagName === "INPUT") return;
      if (!flipped && (event.key === " " || event.key === "Enter")) {
        event.preventDefault(); setFlipped(true); return;
      }
      if (flipped && ["1", "2", "3", "4"].includes(event.key)) {
        grade(Number(event.key));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [flipped, grade]);

  if (!cards) return <div className="center"><span className="spin" /></div>;

  if (!card) {
    return (
      <div className="panel center" style={{ minHeight: 260 }}>
        <div style={{ fontSize: 46 }}>🎉</div>
        <h2>{done} Karten geschafft</h2>
        <p className="sub">Nothing else is due right now.</p>
        <button className="btn" onClick={() => api.flashcards(day, 20)
          .then((d) => { setCards(d.cards); setIndex(0); setDone(0); })}>
          Nochmal laden
        </button>
      </div>
    );
  }

  const glyph = glyphFor(card.lemma);
  const surface = card.gender ? `${card.gender} ${card.lemma}` : card.lemma;

  return (
    <div className="deck">
      <div className="deck__meta">
        <span>{index + 1} / {cards.length}</span>
        {day ? <span className="anchor" style={{ margin: 0 }}>aus Tag {day}</span> : null}
        <span>Rung {card.rung}</span>
      </div>

      <div className={`card ${flipped ? "is-flipped" : ""} ${genderClass(card.gender)}`}
           onClick={() => !flipped && setFlipped(true)}>
        <div className="card__face card__front">
          {glyph ? <div className="card__glyph">{glyph}</div> : null}
          <div className="card__word">
            {card.gender ? <span className="card__art">{card.gender}</span> : null}
            {card.lemma}
          </div>
          <button type="button" className="card__speak"
                  onClick={(e) => { e.stopPropagation(); speak(surface, { rate: 0.8 }); }}>
            🔊
          </button>
          <p className="card__tap">tap or press space</p>
        </div>

        <div className="card__face card__back">
          <p className="card__en">{card.en}</p>
          {card.context_de ? (
            <div className="card__context">
              <span className="card__ctxlabel">du hast es hier gehört</span>
              <GermanText text={card.context_de} onWord={onWord} />
              <p className="card__ctxen">{card.context_en}</p>
            </div>
          ) : null}
        </div>
      </div>

      {flipped ? (
        <div className="grades">
          {GRADES.map((g) => (
            <button key={g.grade} type="button" className={`grade grade--${g.tone}`}
                    onClick={() => grade(g.grade)}>
              <b>{g.label}</b><span>{g.hint}</span><kbd>{g.grade}</kbd>
            </button>
          ))}
        </div>
      ) : (
        <p className="composer__hint">
          Say it out loud first, then flip. Recall beats recognition.
        </p>
      )}
    </div>
  );
}
