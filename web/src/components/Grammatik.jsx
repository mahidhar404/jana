import React, { useCallback, useEffect, useState } from "react";
import GermanText from "./GermanText.jsx";
import { api } from "../lib/api.js";
import { speak } from "../lib/speech.js";

/**
 * Grammar, drilled four ways and scheduled like vocabulary.
 *
 * The map in the left column is the point of this screen. Thirty-four points,
 * in order, with the locked ones visibly locked. A learner who can see that
 * Adjektivdeklination is greyed out until Dativ is done understands the
 * dependency in a way no warning message achieves, and stops trying to
 * brute-force the hardest table in German before he can decline an article.
 *
 * Each point is practised in four shapes, because a rule tested one way is a
 * rule half-learned. `order` in particular is the only honest way to drill
 * German word order; no amount of gap-filling reaches it.
 */
const SHAPES = ["cloze", "transform", "choose", "order"];
const LABEL = { cloze: "Lücke", transform: "Umformen", choose: "Wählen", order: "Ordnen" };

export default function Grammatik({ onWord, day }) {
  const [points, setPoints] = useState(null);
  const [task, setTask] = useState(null);
  const [shape, setShape] = useState("cloze");
  const [answer, setAnswer] = useState("");
  const [picked, setPicked] = useState([]);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    api.grammarProgress().then((d) => setPoints(d.points)).catch(() => setPoints([]));
  }, []);
  useEffect(refresh, [refresh]);

  const open = useCallback(async (pointId) => {
    setBusy(true); setTask(null); setResult(null);
    setAnswer(""); setPicked([]); setShape("cloze");
    try {
      setTask(pointId ? await api.grammarPoint(pointId, day) : await api.grammarNext(day));
    } catch {
      setTask(null);
    } finally {
      setBusy(false);
    }
  }, [day]);

  useEffect(() => { open(null); }, [open]);

  const body = task?.body?.[shape];

  const submit = async () => {
    if (!task || !body) return;
    const response = shape === "order" ? picked.map((t) => t.word).join(" ") : answer;
    setResult(await api.grammarAnswer({
      point_id: task.point.id, shape, response, answer: body.answer,
      exercise_id: task.exercise_id,
    }));
    refresh();
  };

  const nextShape = () => {
    const index = SHAPES.indexOf(shape);
    if (index < SHAPES.length - 1) {
      setShape(SHAPES[index + 1]); setAnswer(""); setPicked([]); setResult(null);
    } else {
      open(null);
    }
  };

  return (
    <div className="grammar">
      <aside className="gmap">
        <h3 className="gmap__title">Grammatik</h3>
        {!points ? <span className="spin" /> : points.map((point) => (
          <button key={point.id} type="button"
                  title={point.locked ? `braucht zuerst: ${point.requires}` : point.description}
                  className={`gpoint ${point.locked ? "is-locked" : ""} ${
                    task?.point?.id === point.id ? "is-current" : ""} ${
                    point.reps >= 3 ? "is-known" : point.started ? "is-started" : ""}`}
                  disabled={point.locked || busy}
                  onClick={() => open(point.id)}>
            <span className="gpoint__lvl">{point.level}</span>
            <span className="gpoint__name">{point.name}</span>
            {point.locked ? <span className="gpoint__lock">🔒</span>
              : point.reps >= 3 ? <span className="gpoint__tick">✓</span> : null}
          </button>
        ))}
      </aside>

      <section className="gwork">
        {busy || !task ? (
          <div className="center"><span className="spin" /></div>
        ) : (
          <div className="panel">
            <p className="anchor" style={{ marginBottom: 12 }}>
              {task.point.level} · {task.point.name}
            </p>
            <p className="sub">{task.point.description}</p>

            {task.point.lesson ? (
              <p className="gvideo">
                🎬 In deinem Kurs: <b>{task.point.lesson.section}</b>
                <span className="muted"> — {task.point.lesson.course}</span>
              </p>
            ) : null}

            <div className="teiltabs" style={{ padding: "14px 0 6px" }}>
              {SHAPES.map((s) => (
                <button key={s} type="button"
                        className={`teiltab ${shape === s ? "is-active" : ""}`}
                        onClick={() => {
                          setShape(s); setAnswer(""); setPicked([]); setResult(null);
                        }}>
                  {LABEL[s]}
                </button>
              ))}
            </div>

            {body ? (
              <Exercise shape={shape} body={body} answer={answer} setAnswer={setAnswer}
                        picked={picked} setPicked={setPicked} result={result}
                        onWord={onWord} />
            ) : null}

            <div className="row">
              {!result ? (
                <button className="btn" onClick={submit}
                        disabled={shape === "order" ? !picked.length : !answer.trim()}>
                  Prüfen
                </button>
              ) : (
                <>
                  <span className={result.correct ? "ok" : "warn"}>
                    {result.correct ? "✓ richtig" : `✗ ${result.expected}`}
                    {result.note ? ` — ${result.note}` : ""}
                  </span>
                  <button className="btn" onClick={nextShape}>
                    {SHAPES.indexOf(shape) < 3 ? "Weiter" : "Nächster Punkt"}
                  </button>
                </>
              )}
            </div>

            {result && body?.why ? <p className="gwhy">💡 {body.why}</p> : null}
            {result?.due_at ? (
              <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                nächste Wiederholung: {new Date(result.due_at).toLocaleDateString()}
                {result.stability_days ? ` · Stabilität ${result.stability_days} Tage` : ""}
              </p>
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}

function Exercise({ shape, body, answer, setAnswer, picked, setPicked, result, onWord }) {
  if (shape === "cloze") {
    const [before, after] = String(body.sentence ?? "").split("___");
    return (
      <div className="gex">
        <p className="de">
          {before}
          <input className="gap" value={answer} disabled={Boolean(result)}
                 placeholder="…" onChange={(e) => setAnswer(e.target.value)} />
          {after}
        </p>
      </div>
    );
  }

  if (shape === "transform") {
    return (
      <div className="gex">
        <GermanText text={body.sentence} onWord={onWord} />
        <p className="ginstr">
          {body.instruction_de}
          <span className="muted"> — {body.instruction_en}</span>
        </p>
        <input className="field" value={answer} disabled={Boolean(result)}
               placeholder="Ihre Umformung…"
               onChange={(e) => setAnswer(e.target.value)} />
      </div>
    );
  }

  if (shape === "choose") {
    return (
      <div className="gex">
        {(body.options ?? []).map((option) => (
          <button key={option} type="button" disabled={Boolean(result)}
                  className={`optbtn ${result
                    ? (option === body.answer ? "is-right"
                      : option === answer ? "is-wrong" : "")
                    : answer === option ? "is-on" : ""}`}
                  onClick={() => setAnswer(option)}>
            {option}
          </button>
        ))}
      </div>
    );
  }

  // order — words carry their index, so a sentence repeating a word still works
  const words = (body.words ?? []).map((word, index) => ({ word, index }));
  const used = new Set(picked.map((t) => t.index));
  return (
    <div className="gex">
      <div className="orderline">
        {picked.length ? picked.map((token) => (
          <button key={token.index} type="button" className="chip" disabled={Boolean(result)}
                  onClick={() => setPicked(picked.filter((t) => t.index !== token.index))}>
            {token.word}
          </button>
        )) : <span className="muted">Wörter unten anklicken</span>}
      </div>
      <div className="pool">
        {words.filter((t) => !used.has(t.index)).map((token) => (
          <button key={token.index} type="button" className="chip chip--pool"
                  disabled={Boolean(result)}
                  onClick={() => setPicked([...picked, token])}>
            {token.word}
          </button>
        ))}
      </div>
      {result ? (
        <button className="btn btn--ghost" style={{ marginTop: 10 }}
                onClick={() => speak(body.answer)}>🔊 richtige Antwort</button>
      ) : null}
    </div>
  );
}
