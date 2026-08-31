import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { speak } from "../lib/speech.js";

/**
 * The card that opens when a German word is clicked.
 *
 * It shows the lemma, not the surface form: click `fährst` and you get
 * `fahren`, because the entry you need to learn is the dictionary one. The
 * resolution happens server-side in jana/wordlookup.py, which ranks candidates
 * by part-of-speech agreement — without that, `gefahren` resolves to `Gefahr`
 * (danger) and the card confidently teaches the wrong word.
 */
export default function WordCard({ word, onClose }) {
  const [entry, setEntry] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setEntry(null);
    setFailed(false);
    api.lookup(word)
      .then((data) => live && setEntry(data))
      .catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [word]);

  return (
    <aside className="wordcard" onClick={(e) => e.stopPropagation()}>
      <header>
        <div>
          <span className="wordcard__lemma">{entry?.lemma ?? word}</span>
          {entry?.gender ? (
            <span className={`gender gender--${entry.gender}`}>{entry.gender}</span>
          ) : null}
          {entry?.pos ? <span className="wordcard__pos">{entry.pos}</span> : null}
        </div>
        <button type="button" className="wordcard__close" onClick={onClose}>✕</button>
      </header>

      {!entry && !failed ? <p className="muted">Nachschlagen…</p> : null}
      {failed ? <p className="muted">Kein Eintrag.</p> : null}

      {entry ? (
        <>
          <p className="wordcard__en">{entry.en}</p>
          {entry.note ? <p className="wordcard__note">{entry.note}</p> : null}
          {entry.plural ? (
            <p className="wordcard__parts">Plural: <b>die {entry.plural}</b></p>
          ) : null}
          {entry.principal_parts ? (
            <p className="wordcard__parts">Stammformen: <b>{entry.principal_parts}</b></p>
          ) : null}
          {entry.cefr ? <span className="chip">{entry.cefr}</span> : null}
          <div className="row">
            <button type="button" className="btn btn--ghost"
                    onClick={() => speak(entry.lemma, { rate: 0.85 })}>🔊 Anhören</button>
            <button type="button" className="btn btn--ghost"
                    onClick={() => speak(entry.lemma, { rate: 0.5 })}>🐢 Langsam</button>
          </div>
        </>
      ) : null}
    </aside>
  );
}
