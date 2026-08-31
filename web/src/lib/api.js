const qs = (params) =>
  Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
    .join("&");

const request = async (path, body) => {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path} → ${response.status}`);
  return response.json();
};

export const api = {
  state: () => request("/api/state"),
  health: () => request("/api/health"),
  session: () => request("/api/session", {}),
  answer: (payload) => request("/api/answer", payload),

  // The exchange is deliberately two calls, not one. See jana/story.py: doing
  // both model calls before responding meant the learner spoke and the scene
  // sat silent for four seconds, then played both lines at once.
  story: (day) => request(day ? `/api/story?day=${day}` : "/api/story"),
  say: (payload) => request("/api/story/say", payload),
  reply: (payload) => request("/api/story/reply", payload),
  dayVocabulary: (day) => request(`/api/story/${day}/vocabulary`),

  lookup: (word) => request(`/api/lookup?word=${encodeURIComponent(word)}`),
  literal: (text) => request("/api/literal", { text }),
  grammar: (text) => request("/api/grammar", { text }),

  lesen: (teil, day) => request(`/api/lesen?${qs({ teil, day })}`),
  lesenAnswer: (payload) => request("/api/lesen/answer", payload),
  hoeren: (teil, day) => request(`/api/hoeren?${qs({ teil, day })}`),
  hoerenAnswer: (payload) => request("/api/hoeren/answer", payload),
  schreiben: (teil, day) => request(`/api/schreiben?${qs({ teil, day })}`),
  schreibenGrade: (payload) => request("/api/schreiben/grade", payload),
  sprechen: (teil, day) => request(`/api/sprechen?${qs({ teil, day })}`),
  recall: (q) => request(`/api/recall?q=${encodeURIComponent(q)}`),
  flashcards: (day, limit = 20) => request(`/api/flashcards?${qs({ day, limit })}`),
  grade: (payload) => request("/api/grade", payload),
  teil: (modul, teil, day) => request(`/api/teil/${modul}/${teil}?${qs({ day })}`),
  grammar: (level) => request(`/api/grammar/curriculum?${qs({ level })}`),
  grammarNext: (day) => request(`/api/grammar/next?${qs({ day })}`),
  grammarPoint: (id, day) => request(`/api/grammar/point/${id}?${qs({ day })}`),
  grammarAnswer: (payload) => request("/api/grammar/answer", payload),
  grammarProgress: () => request("/api/grammar/progress"),
  bank: () => request("/api/bank"),
  audioFor: (text) => request(`/api/audio/for?text=${encodeURIComponent(text)}`),
  audioStats: () => request("/api/audio/stats"),
  hoerenReal: () => request("/api/hoeren/real"),
  sprechenGrade: (payload) => request("/api/sprechen/grade", payload),
};
