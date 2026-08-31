import React, { useEffect, useState } from "react";
import GermanText from "./GermanText.jsx";
import { api } from "../lib/api.js";
import { canListen, listen, speak } from "../lib/speech.js";

const Loading = () => <div className="center"><span className="spin" /></div>;

/** Says where this exercise came from, so the anchoring is visible, not implied. */
const Anchor = ({ day }) => day ? (
  <p className="anchor">🎬 aus Tag {day} — deine heutige Geschichte</p>
) : null;

/* ------------------------------------------------------------------ drill */
export function Drill({ onWord }) {
  const [state, setState] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [typed, setTyped] = useState("");

  const load = () => api.state()
    .then((s) => (s.question ? s : api.session()))
    .then(setState).catch(() => setState({ question: null }));

  useEffect(() => { load(); }, []);

  const answer = async (response) => {
    if (outcome) return;
    const next = await api.answer({
      session_id: state.session_id, item_id: state.question.item_id,
      response, latency_ms: 900,
    });
    setOutcome({ ...next.outcome, next });
  };

  const advance = () => {
    setState(outcome.next); setOutcome(null); setTyped("");
  };

  if (!state) return <Loading />;
  if (!state.question) return (
    <div className="panel"><h2>Nichts mehr fällig</h2>
      <p className="sub">Everything scheduled is in the future.</p></div>
  );

  const q = state.question;
  return (
    <div className="panel">
      <p className="sub">{q.task_type.replace("_", " ")} · rung {q.rung} ·
        {" "}{state.progress?.remaining ?? 0} left</p>
      <GermanText text={q.prompt} onWord={onWord} />
      {q.hint ? <p className="wordcard__parts">{q.hint}</p> : null}

      <div style={{ marginTop: 18 }}>
        {q.options ? q.options.map((option, index) => (
          <button key={option} type="button" className="btn btn--ghost"
                  style={{ display: "block", width: "100%", textAlign: "left",
                           marginBottom: 8,
                           borderColor: outcome
                             ? option === outcome.expected ? "var(--ok)"
                               : "var(--line)" : "var(--line)" }}
                  disabled={Boolean(outcome)} onClick={() => answer(option)}>
            <span className="wordcard__pos">{index + 1}</span> {option}
          </button>
        )) : (
          <input value={typed} onChange={(e) => setTyped(e.target.value)}
                 placeholder="type the German…" disabled={Boolean(outcome)}
                 onKeyDown={(e) => { if (e.key === "Enter") outcome ? advance() : answer(typed); }}
                 style={{ width: "100%", background: "rgba(255,255,255,.05)",
                          border: "1px solid var(--line)", borderRadius: 12,
                          padding: "13px 15px", color: "var(--ink)", font: "inherit",
                          fontSize: 17, outline: "none" }} />
        )}
      </div>

      {outcome ? (
        <div className="row">
          <span style={{ color: outcome.correct ? "var(--ok)" : "var(--bad)" }}>
            {outcome.correct ? "✓ richtig" : `✗ falsch — ${outcome.expected}`}
          </span>
          {outcome.note ? <span className="wordcard__parts">{outcome.note}</span> : null}
          <button type="button" className="btn" onClick={advance}>Weiter</button>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ lesen */
export function Lesen({ onWord, day }) {
  const [teil, setTeil] = useState(1);
  const [material, setMaterial] = useState(null);
  const [marked, setMarked] = useState({});

  useEffect(() => { setMaterial(null); api.lesen(teil, day).then(setMaterial).catch(() => {}); }, [teil, day]);
  if (!material) return <Loading />;
  const body = material.data ?? material;

  return (
    <>
      <TeilTabs value={teil} onChange={setTeil} />
      <div className="panel">
        <Anchor day={day} />
        <Anchor day={day} />
        <Anchor day={day} />
        <Anchor day={day} />
        <GermanText text={body.text} onWord={onWord} />
        <details style={{ marginTop: 14 }}>
          <summary className="muted" style={{ cursor: "pointer" }}>show English</summary>
          <p className="muted">{body.translation ?? body.en}</p>
        </details>
        {(body.questions ?? []).map((question, qi) => (
          <div key={qi} style={{ marginTop: 22, borderTop: "1px solid var(--line)", paddingTop: 16 }}>
            <GermanText text={question.q} onWord={onWord} />
            {(question.options ?? []).map((option) => (
              <button key={option} type="button" className="btn btn--ghost"
                      style={{ display: "block", width: "100%", textAlign: "left", marginTop: 8,
                               borderColor: marked[qi]
                                 ? option === question.answer ? "var(--ok)"
                                   : option === marked[qi] ? "var(--bad)" : "var(--line)"
                                 : "var(--line)" }}
                      disabled={Boolean(marked[qi])}
                      onClick={() => {
                        setMarked((m) => ({ ...m, [qi]: option }));
                        api.lesenAnswer({ response: option, reference: question.answer,
                                          item_ids: material.item_ids ?? [] }).catch(() => {});
                      }}>{option}</button>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ hören */
export function Hoeren({ onWord, day }) {
  const [teil, setTeil] = useState(1);
  const [material, setMaterial] = useState(null);
  const [heard, setHeard] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    setMaterial(null); setResult(null); setHeard("");
    api.hoeren(teil, day).then((data) => {
      setMaterial(data);
      setTimeout(() => speak((data.data ?? data).text, { rate: 0.85 }), 400);
    }).catch(() => {});
  }, [teil, day]);

  if (!material) return <Loading />;
  const body = material.data ?? material;

  return (
    <>
      <TeilTabs value={teil} onChange={setTeil} />
      <div className="panel">
        <h2>🎧 Hören · Teil {teil}</h2>
        <p className="sub">Listen, then type what you heard.</p>
        <div className="row">
          <button className="btn" onClick={() => speak(body.text, { rate: 0.85 })}>▶ Abspielen</button>
          <button className="btn btn--ghost" onClick={() => speak(body.text, { rate: 0.5 })}>🐢 Langsam</button>
        </div>
        <input value={heard} onChange={(e) => setHeard(e.target.value)}
               placeholder="type what you heard…"
               onKeyDown={(e) => { if (e.key === "Enter") check(); }}
               style={{ width: "100%", marginTop: 18, background: "rgba(255,255,255,.05)",
                        border: "1px solid var(--line)", borderRadius: 12, padding: "13px 15px",
                        color: "var(--ink)", font: "inherit", fontSize: 16, outline: "none" }} />
        <div className="row">
          <button className="btn" onClick={check}>Prüfen</button>
        </div>
        {result ? (
          <div style={{ marginTop: 16 }}>
            <p style={{ color: result.correct ? "var(--ok)" : "var(--bad)" }}>
              {(result.accuracy * 100).toFixed(0)}% of the words
              {result.missed?.length ? ` · missed: ${result.missed.join(", ")}` : ""}
            </p>
            <GermanText text={body.text} onWord={onWord} />
            <p className="muted">{body.translation ?? body.en}</p>
          </div>
        ) : null}
      </div>
    </>
  );

  async function check() {
    const outcome = await api.hoerenAnswer({
      response: heard, reference: body.text, item_ids: material.item_ids ?? [],
    });
    setResult(outcome);
  }
}

/* -------------------------------------------------------------- schreiben */
export function Schreiben({ onWord, day }) {
  const [teil, setTeil] = useState(1);
  const [task, setTask] = useState(null);
  const [text, setText] = useState("");
  const [grade, setGrade] = useState(null);
  const [grading, setGrading] = useState(false);

  useEffect(() => { setTask(null); setGrade(null); setText("");
    api.schreiben(teil, day).then(setTask).catch(() => {}); }, [teil, day]);
  if (!task) return <Loading />;

  const words = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <>
      <TeilTabs value={teil} onChange={setTeil} />
      <div className="panel">
        <h2>✍️ Schreiben · Teil {teil}</h2>
        <GermanText text={task.task} onWord={onWord} />
        <p className="muted">{task.en}</p>
        <textarea value={text} onChange={(e) => setText(e.target.value)}
                  placeholder="Hallo …"
                  style={{ width: "100%", minHeight: 170, marginTop: 16, resize: "vertical",
                           background: "rgba(255,255,255,.05)", border: "1px solid var(--line)",
                           borderRadius: 12, padding: "13px 15px", color: "var(--ink)",
                           font: "inherit", fontSize: 16, lineHeight: 1.6, outline: "none" }} />
        <div className="row">
          <button className="btn" disabled={grading || words < 8}
                  onClick={async () => {
                    setGrading(true);
                    try { setGrade(await api.schreibenGrade({ response: text })); }
                    finally { setGrading(false); }
                  }}>{grading ? "Bewertet…" : "Bewerten"}</button>
          <span className="muted">{words} words</span>
        </div>
        {grade && !grade.error ? <Rubric grade={grade} onWord={onWord} /> : null}
        {grade?.error ? <p style={{ color: "var(--bad)" }}>{grade.error}</p> : null}
      </div>
    </>
  );
}

function Rubric({ grade, onWord }) {
  const criteria = [["erfuellung", "Task"], ["kohaerenz", "Coherence"],
                    ["wortschatz", "Vocabulary"], ["strukturen", "Grammar"]];
  return (
    <div style={{ marginTop: 20 }}>
      <div style={{ display: "grid", gap: 10,
                    gridTemplateColumns: "repeat(auto-fit,minmax(112px,1fr))" }}>
        {criteria.map(([key, label]) => (
          <div key={key} style={{ background: "rgba(255,255,255,.04)",
                                  border: "1px solid var(--line)", borderRadius: 12, padding: 12 }}>
            <div style={{ fontSize: 23, fontWeight: 800 }}>{grade[key] ?? 0}
              <span className="muted" style={{ fontSize: 13 }}>/5</span></div>
            <div className="wordcard__pos">{label}</div>
          </div>
        ))}
        <div style={{ background: "rgba(255,154,46,.12)", border: "1px solid var(--saffron)",
                      borderRadius: 12, padding: 12 }}>
          <div style={{ fontSize: 23, fontWeight: 800 }}>{grade.total}
            <span className="muted" style={{ fontSize: 13 }}>/20</span></div>
          <div className="wordcard__pos">Total</div>
        </div>
      </div>
      <p className="muted" style={{ marginTop: 14, fontSize: 14.5 }}>{grade.feedback}</p>
      {(grade.corrections ?? []).map((fix, i) => (
        <p key={i} style={{ fontSize: 14, marginTop: 10 }}>
          <span style={{ color: "var(--bad)", textDecoration: "line-through" }}>{fix.was}</span>
          {" → "}<span style={{ color: "var(--ok)", fontWeight: 600 }}>{fix.should_be}</span>
          <br /><span className="muted">{fix.why}</span>
        </p>
      ))}
      {grade.better_version ? (
        <div style={{ marginTop: 16, background: "rgba(255,255,255,.04)",
                      border: "1px solid var(--line)", borderRadius: 12, padding: 14 }}>
          <div className="wordcard__pos" style={{ marginBottom: 8 }}>Your text, corrected</div>
          <GermanText text={grade.better_version} onWord={onWord} />
        </div>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------- sprechen */
export function Sprechen({ onWord, day }) {
  const [teil, setTeil] = useState(1);
  const [task, setTask] = useState(null);
  const [said, setSaid] = useState("");
  const [listening, setListening] = useState(false);
  const [grade, setGrade] = useState(null);

  useEffect(() => { setTask(null); setGrade(null); setSaid("");
    api.sprechen(teil, day).then(setTask).catch(() => {}); }, [teil, day]);
  if (!task) return <Loading />;

  const body = task.data ?? task;
  const prompt = body.prompt ?? body.topic ?? body.scenario ?? "";

  return (
    <>
      <TeilTabs value={teil} onChange={setTeil} />
      <div className="panel">
        <h2>🎤 Sprechen · Teil {teil}</h2>
        <GermanText text={prompt} onWord={onWord} />
        {body.en ? <p className="muted">{body.en}</p> : null}
        {(body.keywords ?? []).length ? (
          <p className="wordcard__parts">{body.keywords.join(" · ")}</p>
        ) : null}
        <div className="row">
          <button className="btn btn--ghost" onClick={() => speak(prompt)}>🔊 Anhören</button>
          {canListen() ? (
            <button className={`mic ${listening ? "is-live" : ""}`}
                    onClick={() => {
                      if (listening) return;
                      setListening(true);
                      listen({ onPartial: setSaid, onFinal: setSaid,
                               onEnd: () => setListening(false),
                               onError: () => setListening(false) });
                    }}>🎤</button>
          ) : null}
        </div>
        <input value={said} onChange={(e) => setSaid(e.target.value)}
               placeholder="…or type your answer"
               style={{ width: "100%", marginTop: 16, background: "rgba(255,255,255,.05)",
                        border: "1px solid var(--line)", borderRadius: 12, padding: "13px 15px",
                        color: "var(--ink)", font: "inherit", fontSize: 16, outline: "none" }} />
        <div className="row">
          <button className="btn" disabled={!said.trim()}
                  onClick={async () => setGrade(await api.sprechenGrade({ response: said, prompt }))}>
            Bewerten</button>
        </div>
        {grade && !grade.error ? (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{grade.score}
              <span className="muted" style={{ fontSize: 14 }}>/{grade.max}</span></div>
            <p style={{ color: "var(--ok)" }}>✓ {grade.good}</p>
            <p style={{ color: "var(--de-gold)" }}>✎ {grade.fix}</p>
            {grade.model_answer ? (
              <div style={{ marginTop: 12, background: "rgba(255,255,255,.04)",
                            border: "1px solid var(--line)", borderRadius: 12, padding: 14 }}>
                <div className="wordcard__pos" style={{ marginBottom: 8 }}>One good answer</div>
                <GermanText text={grade.model_answer} onWord={onWord} />
                <button className="btn btn--ghost" style={{ marginTop: 10 }}
                        onClick={() => speak(grade.model_answer)}>🔊 Anhören</button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  );
}

function TeilTabs({ value, onChange }) {
  return (
    <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 22 }}>
      {[1, 2, 3].map((n) => (
        <button key={n} type="button"
                className={`btn ${value === n ? "" : "btn--ghost"}`}
                style={{ padding: "8px 18px", fontSize: 13 }}
                onClick={() => onChange(n)}>Teil {n}</button>
      ))}
    </div>
  );
}
