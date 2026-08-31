/**
 * A picture for a word, where a picture is honest.
 *
 * The picture-superiority effect is real, but most B1 vocabulary is abstract —
 * `Lösung`, `Erfahrung`, `höchstens` — and a stock photo for those is decoration
 * pretending to be a memory hook. There is also no image model configured here,
 * so anything generated would be a stand-in for something that does not exist.
 *
 * So: a glyph for concrete words where one genuinely fits, and for everything
 * else the two hooks that actually help in German —
 *   1. the gender colour (der/die/das), a standard mnemonic, applied consistently
 *   2. the sentence from the story where the learner met the word
 * The second is stronger than a stock photo would be, because he was there.
 */
const GLYPHS = {
  // people and family
  mann: "👨", frau: "👩", kind: "🧒", junge: "👦", mädchen: "👧", baby: "👶",
  familie: "👨‍👩‍👧", mutter: "👩", vater: "👨", eltern: "👫", freund: "🧑‍🤝‍🧑",
  freundin: "👭", bruder: "👦", schwester: "👧", oma: "👵", opa: "👴",
  arzt: "👨‍⚕️", ärztin: "👩‍⚕️", lehrer: "👨‍🏫", polizist: "👮", koch: "👨‍🍳",
  // home
  haus: "🏠", wohnung: "🏢", zimmer: "🛏️", küche: "🍳", bad: "🛁", tür: "🚪",
  fenster: "🪟", bett: "🛏️", stuhl: "🪑", tisch: "🪑", lampe: "💡", schlüssel: "🔑",
  garten: "🌳", treppe: "🪜", miete: "💶", heizung: "🔥",
  // food
  brot: "🍞", brötchen: "🥐", käse: "🧀", fleisch: "🥩", fisch: "🐟", ei: "🥚",
  apfel: "🍎", banane: "🍌", obst: "🍇", gemüse: "🥕", kartoffel: "🥔", salat: "🥗",
  suppe: "🍲", kuchen: "🍰", schokolade: "🍫", zucker: "🧂", salz: "🧂",
  wasser: "💧", kaffee: "☕", tee: "🍵", milch: "🥛", bier: "🍺", wein: "🍷",
  essen: "🍽️", trinken: "🥤", restaurant: "🍽️", lokal: "🍻", frühstück: "🥐",
  // travel
  auto: "🚗", zug: "🚆", bus: "🚌", fahrrad: "🚲", flugzeug: "✈️", flughafen: "🛫",
  bahnhof: "🚉", straße: "🛣️", weg: "🛤️", reise: "🧳", koffer: "🧳", ticket: "🎫",
  hotel: "🏨", stadt: "🏙️", dorf: "🏘️", land: "🗺️", karte: "🗺️", lkw: "🚚",
  // work and school
  arbeit: "💼", büro: "🏢", job: "💼", beruf: "🧑‍💼", chef: "🧑‍💼", firma: "🏢",
  schule: "🏫", uni: "🎓", buch: "📕", heft: "📓", stift: "✏️", bleistift: "✏️",
  computer: "💻", handy: "📱", telefon: "📞", brief: "✉️", praktikum: "🧑‍🎓",
  prüfung: "📝", zeugnis: "📜", geld: "💶", bank: "🏦", preis: "🏷️",
  // body and health
  kopf: "🧠", hand: "✋", auge: "👁️", ohr: "👂", mund: "👄", herz: "❤️",
  bein: "🦵", zahn: "🦷", krank: "🤒", gesund: "💪", apotheke: "💊", medikament: "💊",
  // time and weather
  tag: "☀️", nacht: "🌙", morgen: "🌅", abend: "🌆", woche: "📅", monat: "🗓️",
  jahr: "📆", uhr: "🕐", zeit: "⏳", wetter: "🌤️", sonne: "☀️", regen: "🌧️",
  schnee: "❄️", wind: "💨", sommer: "🏖️", winter: "⛄", herbst: "🍂",
  // free time
  musik: "🎵", film: "🎬", sport: "⚽", fußball: "⚽", spiel: "🎲", party: "🎉",
  urlaub: "🏖️", garten2: "🌷", hund: "🐕", katze: "🐈", blume: "🌸", baum: "🌳",
};

const STRIP = ["die ", "der ", "das "];

export function glyphFor(lemma = "") {
  let key = lemma.toLowerCase().trim();
  for (const article of STRIP) if (key.startsWith(article)) key = key.slice(4);
  if (GLYPHS[key]) return GLYPHS[key];
  // German builds compounds freely: Hauptbahnhof ends in Bahnhof.
  for (const [word, glyph] of Object.entries(GLYPHS)) {
    if (word.length >= 4 && key.endsWith(word)) return glyph;
  }
  return null;
}

/** der = blue, die = red, das = green. Used everywhere gender is shown. */
export const genderClass = (gender) => (gender ? `g-${gender}` : "g-none");
