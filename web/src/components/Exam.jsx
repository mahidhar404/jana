import React, { useEffect, useMemo, useRef, useState } from "react";
import GermanText from "./GermanText.jsx";
import { api } from "../lib/api.js";
import { canListen, listen, say, speak, stopSpeaking } from "../lib/speech.js";

/**
 * The four exam modules, one component per Teil format.
 *
 * Each Teil is a different *interaction*, not a different prompt in the same
 * shell — matching, taking sides, true/false, attributing a line to a speaker.
 * That is deliberate: the formats are fixed and published, meeting one cold on
 * exam day costs points, and the muscle memory for "what am I being asked to do
 * here" is worth as much as the German.
 */
const Loading = () => <div className="center"><span className="spin" /></div>;

/** Says whether that was a person or the synthesiser. Provenance, for audio. */
const VoiceBadge = ({ source }) => (
  <span className={`voicebadge voicebadge--${source}`}>
    {source === "recording" ? "🎙️ echte Aufnahme" : "🔉 Synthese"}
  </span>
);

const Head = ({ task, day, children }) => (
  <div className="panel">
    {day ? <p className="anchor">🎬 aus Tag {day} — deine Geschichte</p> : null}
    <p className="sub" style={{ marginBottom: 8 }}>{task.instruction_en}</p>
    <p className="exam__instruction">{task.instruction_de}</p>
    {children}
  </div>
);

function useTeil(modul, teil, day) {
  const [task, setTask] = useState(null);
  useEffect(() => {
    let live = true;
    setTask(null);
    api.teil(modul, teil, day).then((t) => live && setTask(t)).catch(() => {});
    return () => { live = false; stopSpeaking(); };
  }, [modul, teil, day]);
  return task;
}

const TeilTabs = ({ value, onChange, count }) => (
  <div className="teiltabs">
    {Array.from({ length: count }, (_, i) => i + 1).map((n) => (
      <button key={n} type="button"
              className={`teiltab ${value === n ? "is-active" : ""}`}
              onClick={() => onChange(n)}>Teil {n}</button>
    ))}
  </div>
);

/* =============================================================== LESEN */
export function Lesen({ onWord, day }) {
  const [teil, setTeil] = useState(3);
  const task = useTeil("lesen", teil, day);
  return (
    <>
      <TeilTabs value={teil} onChange={setTeil} count={5} />
      {!task ? <Loading /> : teil === 3 ? <Matching task={task} day={day} onWord={onWord} />
        : teil === 4 ? <Stances task={task} day={day} onWord={onWord} />
        : teil === 5 ? <Rules task={task} day={day} onWord={onWord} />
        : <Passage task={task} day={day} onWord={onWord} />}
    </>
  );
}

/** Teil 3 — match a situation to an ad, or to "x" when none fits. */
function Matching({ task, day, onWord }) {
  const { ads = [], situations = [] } = task.body;
  const [picked, setPicked] = useState({});
  const [checked, setChecked] = useState(false);
  const score = situations.filter((s, i) => picked[i] === s.answer).length;

  return (
    <Head task={task} day={day}>
      <div className="ads">
        {ads.map((ad) => (
          <div key={ad.key} className="ad">
            <span className="ad__key">{ad.key}</span>
            <GermanText text={ad.text} onWord={onWord} />
          </div>
        ))}
      </div>
      <div className="situations">
        {situations.map((situation, index) => {
          const right = picked[index] === situation.answer;
          return (
            <div key={index} className={`situation ${checked ? (right ? "is-right" : "is-wrong") : ""}`}>
              <GermanText text={situation.text} onWord={onWord} />
              <div className="keys">
                {[...ads.map((a) => a.key), "x"].map((key) => (
                  <button key={key} type="button" disabled={checked}
                          className={`key ${picked[index] === key ? "is-on" : ""} ${
                            checked && key === situation.answer ? "is-answer" : ""}`}
                          onClick={() => setPicked((p) => ({ ...p, [index]: key }))}>
                    {key}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <div className="row">
        {!checked ? (
          <button className="btn" disabled={Object.keys(picked).length < situations.length}
                  onClick={() => setChecked(true)}>Prüfen</button>
        ) : (
          <span className={score === situations.length ? "ok" : "warn"}>
            {score} / {situations.length} richtig
          </span>
        )}
        <span className="muted">x = keine Anzeige passt</span>
      </div>
    </Head>
  );
}

/** Teil 4 — for or against. */
function Stances({ task, day, onWord }) {
  const { question, opinions = [] } = task.body;
  const [picked, setPicked] = useState({});
  const [checked, setChecked] = useState(false);
  const score = opinions.filter((o, i) => picked[i] === o.stance).length;

  return (
    <Head task={task} day={day}>
      <h2 style={{ marginTop: 10 }}>{question}</h2>
      <p className="muted">{task.body.en}</p>
      {opinions.map((opinion, index) => {
        const right = picked[index] === opinion.stance;
        return (
          <div key={index} className={`opinion ${checked ? (right ? "is-right" : "is-wrong") : ""}`}>
            <span className="opinion__name">{opinion.name}</span>
            <GermanText text={opinion.text} onWord={onWord} />
            <div className="keys">
              {["dafür", "dagegen"].map((stance) => (
                <button key={stance} type="button" disabled={checked}
                        className={`key key--wide ${picked[index] === stance ? "is-on" : ""} ${
                          checked && stance === opinion.stance ? "is-answer" : ""}`}
                        onClick={() => setPicked((p) => ({ ...p, [index]: stance }))}>
                  {stance === "dafür" ? "👍 dafür" : "👎 dagegen"}
                </button>
              ))}
            </div>
          </div>
        );
      })}
      <div className="row">
        {!checked ? (
          <button className="btn" disabled={Object.keys(picked).length < opinions.length}
                  onClick={() => setChecked(true)}>Prüfen</button>
        ) : <span className={score === opinions.length ? "ok" : "warn"}>
              {score} / {opinions.length} richtig</span>}
      </div>
    </Head>
  );
}

/** Teil 5 — a set of rules, then detail questions. */
function Rules({ task, day, onWord }) {
  const { title, rules = [], questions = [] } = task.body;
  const [picked, setPicked] = useState({});
  return (
    <Head task={task} day={day}>
      <div className="rules">
        <h2>{title}</h2>
        <ol>{rules.map((rule, i) => (
          <li key={i}><GermanText text={rule} onWord={onWord} /></li>
        ))}</ol>
      </div>
      {questions.map((question, qi) => (
        <div key={qi} className="question">
          <GermanText text={question.q} onWord={onWord} />
          {(question.options ?? []).map((option) => (
            <button key={option} type="button"
                    className={`optbtn ${picked[qi] ? (option === question.answer ? "is-right"
                      : option === picked[qi] ? "is-wrong" : "") : ""}`}
                    disabled={Boolean(picked[qi])}
                    onClick={() => setPicked((p) => ({ ...p, [qi]: option }))}>
              {option}
            </button>
          ))}
        </div>
      ))}
    </Head>
  );
}

/** Teil 1 and 2 — a passage with comprehension questions. */
function Passage({ task, day, onWord }) {
  const body = task.body?.data ?? task.body ?? {};
  const [picked, setPicked] = useState({});
  return (
    <Head task={{ ...task, instruction_de: task.instruction_de ?? "Lesen Sie den Text.",
                  instruction_en: task.instruction_en ?? "Read the text." }} day={day}>
      <GermanText text={body.text} onWord={onWord} />
      <details style={{ marginTop: 12 }}>
        <summary className="muted" style={{ cursor: "pointer" }}>show English</summary>
        <p className="muted">{body.translation ?? body.en}</p>
      </details>
      {(body.questions ?? []).map((question, qi) => (
        <div key={qi} className="question">
          <GermanText text={question.q} onWord={onWord} />
          {(question.options ?? []).map((option) => (
            <button key={option} type="button"
                    className={`optbtn ${picked[qi] ? (option === question.answer ? "is-right"
                      : option === picked[qi] ? "is-wrong" : "") : ""}`}
                    disabled={Boolean(picked[qi])}
                    onClick={() => setPicked((p) => ({ ...p, [qi]: option }))}>
              {option}
            </button>
          ))}
        </div>
      ))}
    </Head>
  );
}

/* =============================================================== HÖREN */
export function Hoeren({ onWord, day }) {
  const [teil, setTeil] = useState(0);
  const task = useTeil("hoeren", teil, day);
  return (
    <>
      <div className="teiltabs">
        <button type="button" className={`teiltab teiltab--real ${teil === 0 ? "is-active" : ""}`}
                onClick={() => setTeil(0)}>🎙️ Echte Aufnahme</button>
        {[1, 2, 3, 4].map((n) => (
          <button key={n} type="button"
                  className={`teiltab ${teil === n ? "is-active" : ""}`}
                  onClick={() => setTeil(n)}>Teil {n}</button>
        ))}
      </div>
      {teil === 0 ? <RealAudio onWord={onWord} />
        : !task ? <Loading />
        : teil === 2 ? <SingleListen task={task} day={day} onWord={onWord} />
        : teil === 4 ? <WhoSaidIt task={task} day={day} onWord={onWord} />
        : <Dictation task={task} day={day} onWord={onWord} />}
    </>
  );
}

/**
 * Dictation built from a real recording, not from generated text.
 *
 * This is the right way round. Generating a sentence and hoping a recording of
 * it exists gets synthesis most of the time; starting from a clip guarantees a
 * human voice — and these clips come from the Goethe listening material and the
 * German-dense courses, which is the register the exam actually uses.
 */
function RealAudio({ onWord }) {
  const [clip, setClip] = useState(null);
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState(null);
  const player = useRef(null);

  const load = () => {
    setClip(null); setTyped(""); setResult(null);
    api.hoerenReal().then((t) => setClip(t)).catch(() => setClip({ error: true }));
  };
  useEffect(load, []);

  if (!clip) return <Loading />;
  if (clip.error) return (
    <div className="panel"><h2>Keine Aufnahmen</h2>
      <p className="sub">Run <code>uv run python -m jana.ingest.clips --exam</code> first.</p>
    </div>
  );

  const body = clip.body;
  const check = () => {
    const said = typed.toLowerCase().replace(/[^\wäöüß\s]/g, " ").split(/\s+/).filter(Boolean);
    const want = body.text.toLowerCase().replace(/[^\wäöüß\s]/g, " ").split(/\s+/).filter(Boolean);
    const hit = want.filter((w) => said.includes(w));
    setResult({ accuracy: want.length ? hit.length / want.length : 0,
                missed: want.filter((w) => !said.includes(w)) });
  };

  return (
    <div className="panel">
      <p className={`anchor ${body.is_exam_audio ? "anchor--exam" : ""}`}>
        {body.is_exam_audio ? "🏛️ echtes Goethe-Prüfungsmaterial" : `🎬 ${body.origin}`}
      </p>
      <p className="exam__instruction">{clip.instruction_de}</p>
      <p className="sub">{clip.instruction_en}</p>

      <div className="listen">
        <button className="playbig" onClick={() => { player.current.currentTime = 0;
                                                     player.current.play(); }}>▶</button>
        <button className="btn btn--ghost"
                onClick={() => { player.current.playbackRate = 0.65;
                                 player.current.currentTime = 0;
                                 player.current.play(); }}>🐢 Langsam</button>
        <span className="muted">{body.seconds}s</span>
        <VoiceBadge source="recording" />
        <audio ref={player} src={body.url} preload="auto"
               onPlay={(e) => { if (e.target.playbackRate !== 0.65) e.target.playbackRate = 1; }} />
      </div>

      <input className="field" value={typed} placeholder="Was haben Sie gehört?"
             onChange={(e) => setTyped(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") result ? load() : check(); }} />
      <div className="row">
        <button className="btn" onClick={result ? load : check}>
          {result ? "Nächste Aufnahme" : "Prüfen"}
        </button>
      </div>

      {result ? (
        <div style={{ marginTop: 16 }}>
          <p className={result.accuracy >= 0.8 ? "ok" : "warn"}>
            {(result.accuracy * 100).toFixed(0)}% der Wörter
            {result.missed.length ? ` · fehlt: ${result.missed.join(", ")}` : ""}
          </p>
          <GermanText text={body.text} onWord={onWord} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Teil 2 — heard exactly once.
 *
 * The single-listen rule *is* the difficulty of this Teil, so the play button
 * disables itself after one use. Practising it with replay available would
 * train the wrong thing.
 */
function SingleListen({ task, day, onWord }) {
  const { text, statements = [] } = task.body;
  const [played, setPlayed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [heardVia, setHeardVia] = useState(null);
  const [picked, setPicked] = useState({});
  const [revealed, setRevealed] = useState(false);
  const score = statements.filter((s, i) => picked[i] === s.true).length;

  const play = async () => {
    setPlayed(true); setPlaying(true);
    const outcome = await say(text, { rate: 0.92 });
    setHeardVia(outcome.source);
    setPlaying(false);
  };

  return (
    <Head task={task} day={day}>
      <div className="listen">
        <button className={`playbig ${playing ? "is-playing" : ""}`}
                disabled={played} onClick={play}>
          {playing ? <span className="bars"><i /><i /><i /><i /></span> : "▶"}
        </button>
        <p className="muted">
          {played ? "Sie haben den Text gehört. Kein zweites Mal." : "Nur EINMAL hörbar"}
          {heardVia ? <VoiceBadge source={heardVia} /> : null}
        </p>
      </div>
      {statements.map((statement, index) => (
        <div key={index} className={`statement ${revealed
          ? (picked[index] === statement.true ? "is-right" : "is-wrong") : ""}`}>
          <GermanText text={statement.text} onWord={onWord} />
          <div className="keys">
            {[true, false].map((value) => (
              <button key={String(value)} type="button" disabled={revealed}
                      className={`key key--wide ${picked[index] === value ? "is-on" : ""} ${
                        revealed && value === statement.true ? "is-answer" : ""}`}
                      onClick={() => setPicked((p) => ({ ...p, [index]: value }))}>
                {value ? "✓ richtig" : "✗ falsch"}
              </button>
            ))}
          </div>
        </div>
      ))}
      <div className="row">
        {!revealed ? (
          <button className="btn" disabled={Object.keys(picked).length < statements.length}
                  onClick={() => setRevealed(true)}>Prüfen</button>
        ) : (
          <>
            <span className={score === statements.length ? "ok" : "warn"}>
              {score} / {statements.length}
            </span>
            <div className="transcriptbox">
              <span className="card__ctxlabel">Transkript</span>
              <GermanText text={text} onWord={onWord} />
              <p className="muted">{task.body.en}</p>
            </div>
          </>
        )}
      </div>
    </Head>
  );
}

/** Teil 4 — who holds this view: A, B, or both. */
function WhoSaidIt({ task, day, onWord }) {
  const { turns = [], statements = [], a_name: a = "A", b_name: b = "B" } = task.body;
  const [picked, setPicked] = useState({});
  const [revealed, setRevealed] = useState(false);
  const [heard, setHeard] = useState(false);
  const score = statements.filter((s, i) => picked[i] === s.who).length;

  const playAll = async () => {
    setHeard(true);
    for (const turn of turns) {
      await speak(turn.text, { rate: 0.92, pitch: turn.speaker === "a" ? 0.95 : 1.12 });
    }
  };

  return (
    <Head task={task} day={day}>
      <div className="listen">
        <button className="playbig" onClick={playAll}>▶</button>
        <p className="muted">{a} und {b} diskutieren</p>
      </div>
      {heard ? (
        <div className="dialogue">
          {turns.map((turn, i) => (
            <div key={i} className={`dline dline--${turn.speaker}`}>
              <span className="dline__who">{turn.speaker === "a" ? a : b}</span>
              <GermanText text={turn.text} onWord={onWord} />
            </div>
          ))}
        </div>
      ) : null}
      {statements.map((statement, index) => (
        <div key={index} className={`statement ${revealed
          ? (picked[index] === statement.who ? "is-right" : "is-wrong") : ""}`}>
          <GermanText text={statement.text} onWord={onWord} />
          <div className="keys">
            {[["a", a], ["b", b], ["beide", "beide"]].map(([value, label]) => (
              <button key={value} type="button" disabled={revealed}
                      className={`key key--wide ${picked[index] === value ? "is-on" : ""} ${
                        revealed && value === statement.who ? "is-answer" : ""}`}
                      onClick={() => setPicked((p) => ({ ...p, [index]: value }))}>
                {label}
              </button>
            ))}
          </div>
        </div>
      ))}
      <div className="row">
        {!revealed ? (
          <button className="btn" disabled={Object.keys(picked).length < statements.length}
                  onClick={() => setRevealed(true)}>Prüfen</button>
        ) : <span className={score === statements.length ? "ok" : "warn"}>
              {score} / {statements.length}</span>}
      </div>
    </Head>
  );
}

/** Teil 1 and 3 — dictation: hear it, write it. */
function Dictation({ task, day, onWord }) {
  const body = task.body?.data ?? task.body ?? {};
  const [heard, setHeard] = useState("");
  const [result, setResult] = useState(null);
  const [via, setVia] = useState(null);
  useEffect(() => {
    if (body.text) setTimeout(() => say(body.text, { rate: 0.85 }).then((o) => setVia(o.source)), 400);
  }, [body.text]);
  return (
    <Head task={{ ...task, instruction_de: task.instruction_de ?? "Was haben Sie gehört?",
                  instruction_en: task.instruction_en ?? "Type what you heard." }} day={day}>
      <div className="listen">
        <button className="playbig"
                onClick={() => say(body.text, { rate: 0.85 }).then((o) => setVia(o.source))}>▶</button>
        <button className="btn btn--ghost" onClick={() => speak(body.text, { rate: 0.5 })}>
          🐢 Langsam
        </button>
        {via ? <VoiceBadge source={via} /> : null}
      </div>
      <input className="field" value={heard} placeholder="tippen Sie, was Sie gehört haben…"
             onChange={(e) => setHeard(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") check(); }} />
      <div className="row"><button className="btn" onClick={check}>Prüfen</button></div>
      {result ? (
        <div style={{ marginTop: 14 }}>
          <p className={result.correct ? "ok" : "warn"}>
            {(result.accuracy * 100).toFixed(0)}% der Wörter
            {result.missed?.length ? ` · fehlt: ${result.missed.join(", ")}` : ""}
          </p>
          <GermanText text={body.text} onWord={onWord} />
          <p className="muted">{body.translation ?? body.en}</p>
        </div>
      ) : null}
    </Head>
  );

  async function check() {
    setResult(await api.hoerenAnswer({
      response: heard, reference: body.text, item_ids: body.item_ids ?? [], teil: 3,
    }));
  }
}

/* =========================================================== SCHREIBEN */
export function Schreiben({ onWord, day }) {
  const [teil, setTeil] = useState(3);
  const task = useTeil("schreiben", teil, day);
  const [text, setText] = useState("");
  const [grade, setGrade] = useState(null);
  const [grading, setGrading] = useState(false);
  useEffect(() => { setText(""); setGrade(null); }, [teil, day]);

  if (!task) return <><TeilTabs value={teil} onChange={setTeil} count={3} /><Loading /></>;
  const body = task.body?.data ?? task.body ?? {};
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const target = teil === 3 ? 40 : 80;
  const covered = (body.must_cover ?? []).map((point) => ({
    point, done: text.length > 20,
  }));

  return (
    <>
      <TeilTabs value={teil} onChange={setTeil} count={3} />
      <Head task={{ ...task, instruction_de: task.instruction_de ?? "Schreiben Sie.",
                    instruction_en: task.instruction_en ?? "Write." }} day={day}>
        <div className="taskbox">
          <GermanText text={body.task} onWord={onWord} />
          <p className="muted">{body.en}</p>
          {body.to ? <p className="wordcard__parts">An: {body.to}</p> : null}
        </div>
        {covered.length ? (
          <ul className="checklist">
            {covered.map(({ point, done }) => (
              <li key={point} className={done ? "is-done" : ""}>{done ? "✓" : "○"} {point}</li>
            ))}
          </ul>
        ) : null}
        <textarea className="field field--area" value={text} placeholder="Sehr geehrte…"
                  onChange={(e) => setText(e.target.value)} />
        <div className="row">
          <button className="btn" disabled={grading || words < 8}
                  onClick={async () => {
                    setGrading(true);
                    try { setGrade(await api.schreibenGrade({ response: text, teil })); }
                    finally { setGrading(false); }
                  }}>{grading ? "Bewertet…" : "Bewerten"}</button>
          <span className={words >= target ? "ok" : "muted"}>
            {words} / {target} Wörter
          </span>
        </div>
        {grade && !grade.error ? <Rubric grade={grade} onWord={onWord} /> : null}
      </Head>
    </>
  );
}

function Rubric({ grade, onWord }) {
  const criteria = [["erfuellung", "Erfüllung"], ["kohaerenz", "Kohärenz"],
                    ["wortschatz", "Wortschatz"], ["strukturen", "Strukturen"]];
  return (
    <div style={{ marginTop: 20 }}>
      <div className="scoregrid">
        {criteria.map(([key, label]) => (
          <div key={key} className="scorebox">
            <div className="scorebox__n">{grade[key] ?? 0}<small>/5</small></div>
            <div className="wordcard__pos">{label}</div>
          </div>
        ))}
        <div className="scorebox scorebox--total">
          <div className="scorebox__n">{grade.total}<small>/20</small></div>
          <div className="wordcard__pos">Gesamt</div>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 14.5 }}>{grade.feedback}</p>
      {(grade.corrections ?? []).map((fix, i) => (
        <p key={i} className="fixline">
          <span className="was">{fix.was}</span> → <span className="now">{fix.should_be}</span>
          <br /><span className="muted">{fix.why}</span>
        </p>
      ))}
      {grade.better_version ? (
        <div className="taskbox" style={{ marginTop: 14 }}>
          <span className="card__ctxlabel">dein Text, korrigiert</span>
          <GermanText text={grade.better_version} onWord={onWord} />
        </div>
      ) : null}
    </div>
  );
}

/* ============================================================ SPRECHEN */
export function Sprechen({ onWord, day }) {
  const [teil, setTeil] = useState(3);
  const task = useTeil("sprechen", teil, day);
  const [said, setSaid] = useState("");
  const [listening, setListening] = useState(false);
  const [grade, setGrade] = useState(null);
  const recognition = useRef(null);
  useEffect(() => { setSaid(""); setGrade(null); }, [teil, day]);

  if (!task) return <><TeilTabs value={teil} onChange={setTeil} count={3} /><Loading /></>;
  const body = task.body?.data ?? task.body ?? {};
  const prompt = body.presentation ?? body.prompt ?? body.topic ?? body.scenario ?? "";

  const toggle = () => {
    if (listening) { recognition.current?.stop(); return; }
    recognition.current = listen({
      onPartial: setSaid, onFinal: setSaid,
      onEnd: () => setListening(false), onError: () => setListening(false),
    });
    if (recognition.current) setListening(true);
  };

  return (
    <>
      <TeilTabs value={teil} onChange={setTeil} count={3} />
      <Head task={{ ...task, instruction_de: task.instruction_de ?? "Sprechen Sie.",
                    instruction_en: task.instruction_en ?? "Speak." }} day={day}>
        {body.speaker ? (
          <div className="partner">
            <div className="partner__who">🧑 {body.speaker} · {body.topic}</div>
            <GermanText text={body.presentation} onWord={onWord} />
            <p className="muted">{body.en}</p>
            <button className="btn btn--ghost"
                    onClick={() => speak(body.presentation, { rate: 0.9 })}>
              🔊 Präsentation anhören
            </button>
          </div>
        ) : (
          <>
            <GermanText text={prompt} onWord={onWord} />
            <p className="muted">{body.en}</p>
            <button className="btn btn--ghost" onClick={() => speak(prompt)}>🔊 Anhören</button>
          </>
        )}
        {(body.expects ?? body.keywords ?? []).length ? (
          <ul className="checklist">
            {(body.expects ?? body.keywords).map((point) => (
              <li key={point}>○ {point}</li>
            ))}
          </ul>
        ) : null}
        <div className="row">
          {canListen() ? (
            <button className={`mic ${listening ? "is-live" : ""}`} onClick={toggle}>🎤</button>
          ) : null}
          <input className="field" value={said} placeholder="…oder tippen Sie Ihre Antwort"
                 onChange={(e) => setSaid(e.target.value)} />
        </div>
        <div className="row">
          <button className="btn" disabled={!said.trim()}
                  onClick={async () => setGrade(
                    await api.sprechenGrade({ response: said, prompt, teil }))}>
            Bewerten
          </button>
        </div>
        {grade && !grade.error ? (
          <div style={{ marginTop: 16 }}>
            <div className="scorebox scorebox--total" style={{ display: "inline-block" }}>
              <div className="scorebox__n">{grade.score}<small>/{grade.max}</small></div>
            </div>
            <p className="ok">✓ {grade.good}</p>
            <p className="warn">✎ {grade.fix}</p>
            {grade.model_answer ? (
              <div className="taskbox">
                <span className="card__ctxlabel">eine gute Antwort</span>
                <GermanText text={grade.model_answer} onWord={onWord} />
                <button className="btn btn--ghost" style={{ marginTop: 10 }}
                        onClick={() => speak(grade.model_answer)}>🔊</button>
              </div>
            ) : null}
          </div>
        ) : null}
      </Head>
    </>
  );
}
