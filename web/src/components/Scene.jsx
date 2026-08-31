import React, { useEffect, useMemo, useState } from "react";
import Character from "./Character.jsx";
import GermanText from "./GermanText.jsx";

/**
 * The stage: two people meet on a street and talk.
 *
 * The scene is a state machine with four phases — approach, meet, talk, idle —
 * because the animation and the conversation have to agree about who is
 * speaking. Driving it from a single `phase` plus a `speaker` is the reason the
 * bubbles, the walk cycle and the audio stay in step; each is a function of the
 * same state rather than three timers hoping to line up.
 *
 * German goes in a bubble over whoever is speaking. English goes in one place
 * only: a subtitle band at the bottom. That split is deliberate — a learner who
 * can find the translation next to every German sentence will read the
 * translation, so the eye has to leave the speaker to get it.
 */
const SKY = {
  Reisen: ["#2b3a67", "#5b7fb8", "#f2b880"],
  Wohnen: ["#1f2b45", "#3d5a80", "#d9a066"],
  Arbeit: ["#22304d", "#4a6fa5", "#c9d6e8"],
  Gesundheit: ["#243447", "#4f7a8c", "#e8dcc8"],
  Einkaufen: ["#2d2f4a", "#6b5b95", "#e6a57e"],
  Behörden: ["#2a2d3e", "#4b5372", "#b8c0d4"],
  Bildung: ["#232f4b", "#46628f", "#e0c9a6"],
  default: ["#1b2340", "#3f5b8f", "#e3a76f"],
};

export default function Scene({ day, turns, speaker, phase, subtitle, onWord,
                               waiting = false, npcName, onAdvance }) {
  // The last thing each side said, kept on screen after they finish saying it.
  //
  // Previously a bubble was rendered only while `speaker` matched, so the
  // German vanished the moment the turn ended — exactly when the learner wants
  // to read it. Reading is slower than listening, and the sentence he just
  // produced is the single most useful thing on the screen. It stays until it
  // is replaced.
  const [entered, setEntered] = useState(false);
  const [dusk, dawn, glow] = SKY[day?.theme] ?? SKY.default;

  useEffect(() => {
    setEntered(false);
    const timer = setTimeout(() => setEntered(true), 60);
    return () => clearTimeout(timer);
  }, [day?.day_number]);

  const latest = useMemo(() => {
    const spoken = { learner: null, npc: null };
    for (const turn of turns) spoken[turn.speaker] = turn;
    return spoken;
  }, [turns]);

  const stagePhase = entered ? phase : "approach";
  const walking = stagePhase === "approach";

  return (
    <div className="scene" style={{ "--dusk": dusk, "--dawn": dawn, "--glow": glow }}>
      <div className="scene__sky" />
      <div className="scene__sun" />
      <div className="scene__far" />
      <div className={`scene__mid ${walking ? "is-scrolling" : ""}`} />
      <div className={`scene__near ${walking ? "is-scrolling" : ""}`} />
      <div className="scene__haze" />

      <div className="scene__place">
        <span className="scene__day">Tag {day?.day_number}</span>
        <span className="scene__title">{day?.title}</span>
        <span className="scene__where">{day?.setting}</span>
      </div>

      <div className={`stage stage--${stagePhase}`}>
        <div className="actor actor--learner">
          {latest.learner && (
            <Bubble side="left" turn={latest.learner} onWord={onWord}
                    live={speaker === "learner"} />
          )}
          <Character
            variant="learner"
            speaking={speaker === "learner"}
            phase={walking ? "walking" : speaker === "learner" ? "talking" : "idle"}
          />
          <span className="actor__name">Du</span>
        </div>

        <div className="actor actor--npc">
          {latest.npc && (
            <Bubble side="right" turn={latest.npc} onWord={onWord}
                    live={speaker === "npc"} />
          )}
          <Character
            variant="npc"
            flip
            speaking={speaker === "npc"}
            mood={waiting ? "puzzled" : "neutral"}
            phase={walking ? "walking" : speaker === "npc" ? "talking" : "idle"}
          />
          {waiting ? (
            <button type="button" className="waiting" onClick={onAdvance}>
              <span className="waiting__dots"><i /><i /><i /></span>
              {npcName?.split(" ")[0] ?? "Sie"} will antworten
            </button>
          ) : null}
          <span className="actor__name">{day?.npc}</span>
        </div>
      </div>

      <div className="scene__ground" />

      <div className={`subtitle ${subtitle ? "is-on" : ""}`}>
        <span>{subtitle}</span>
      </div>
    </div>
  );
}

function Bubble({ side, turn, onWord, live }) {
  return (
    <div className={`bubble bubble--${side} ${live ? "is-live" : "is-held"}`}>
      <GermanText text={turn.de} onWord={onWord} />
      {turn.correction ? <p className="bubble__fix">✎ {turn.correction}</p> : null}
    </div>
  );
}
