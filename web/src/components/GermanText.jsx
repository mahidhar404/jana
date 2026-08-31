import React from "react";

const TOKEN = /([A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß]+)*)/g;

/** German with every word clickable. Punctuation stays inert. */
export default function GermanText({ text, onWord, className = "" }) {
  if (!text) return null;
  const parts = String(text).split(TOKEN);
  return (
    <p className={`de ${className}`}>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <button
            key={index}
            type="button"
            className="tok"
            onClick={(event) => { event.stopPropagation(); onWord?.(part); }}
          >
            {part}
          </button>
        ) : (
          <span key={index}>{part}</span>
        )
      )}
    </p>
  );
}
