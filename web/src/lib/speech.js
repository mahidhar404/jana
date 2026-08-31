// Browser speech. The server never touches audio — see jana/modules.py for why.

let cachedVoice = null;

const germanVoice = () => {
  if (cachedVoice) return cachedVoice;
  const voices = window.speechSynthesis?.getVoices() ?? [];
  cachedVoice =
    voices.find((v) => v.lang === "de-DE" && v.localService) ||
    voices.find((v) => v.lang?.startsWith("de")) ||
    null;
  return cachedVoice;
};

if (typeof window !== "undefined" && window.speechSynthesis) {
  window.speechSynthesis.onvoiceschanged = () => { cachedVoice = null; germanVoice(); };
  germanVoice();
}

export const hasGermanVoice = () => Boolean(germanVoice());

/** Speak German. Resolves when the utterance ends, so the scene can wait on it. */
export const speak = (text, { rate = 0.9, pitch = 1 } = {}) =>
  new Promise((resolve) => {
    if (!window.speechSynthesis || !text) return resolve();
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "de-DE";
    utterance.rate = rate;
    utterance.pitch = pitch;
    const voice = germanVoice();
    if (voice) utterance.voice = voice;
    utterance.onend = resolve;
    utterance.onerror = resolve;
    window.speechSynthesis.speak(utterance);
  });

export const stopSpeaking = () => {
  window.speechSynthesis?.cancel();
  if (current) { current.pause(); current = null; }
};

let current = null;

/**
 * Say a German sentence — with a real human recording when one exists.
 *
 * The operating system voice will pronounce anything, identically, forever. It
 * has one speaker, flat prosody and no sense of sentence stress, and the exam
 * plays announcements and discussions by different people at natural pace.
 *
 * So the server is asked first whether it holds a recording of this exact
 * sentence (whisper-aligned from the Goethe listening material and the course
 * corpus). If it does, the learner hears a person. If it does not — and Jana
 * generates fresh German constantly, so it often will not — synthesis fills in.
 *
 * Falls back on every failure path: no clip, network error, audio blocked by
 * autoplay policy. Being briefly robotic is survivable; being silent is not.
 */
export const say = async (text, { rate = 0.9, pitch = 1, preferReal = true } = {}) => {
  if (!text) return { source: "none" };
  if (preferReal) {
    try {
      const response = await fetch(`/api/audio/for?text=${encodeURIComponent(text)}`);
      if (response.ok) {
        const { clip } = await response.json();
        if (clip?.url) {
          stopSpeaking();
          const player = new Audio(clip.url);
          player.playbackRate = Math.min(1, Math.max(0.5, rate + 0.1));
          current = player;
          await player.play();
          return new Promise((resolve) => {
            player.onended = () => resolve({ source: "recording", clip });
            player.onerror = async () => { await speak(text, { rate, pitch });
                                           resolve({ source: "synthesis" }); };
          });
        }
      }
    } catch { /* fall through to synthesis */ }
  }
  await speak(text, { rate, pitch });
  return { source: "synthesis" };
};

const Recognition =
  typeof window !== "undefined" &&
  (window.SpeechRecognition || window.webkitSpeechRecognition);

export const canListen = () => Boolean(Recognition);

export const listen = ({ onPartial, onFinal, onEnd, onError }) => {
  if (!Recognition) return null;
  const recognition = new Recognition();
  recognition.lang = "de-DE";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.onresult = (event) => {
    const text = Array.from(event.results).map((r) => r[0].transcript).join(" ");
    const done = event.results[event.results.length - 1].isFinal;
    (done ? onFinal : onPartial)?.(text);
  };
  recognition.onend = () => onEnd?.();
  recognition.onerror = (event) => onError?.(event.error);
  recognition.start();
  return recognition;
};
