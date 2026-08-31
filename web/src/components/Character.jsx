import React from "react";

/**
 * A figure with a face, drawn rather than loaded.
 *
 * There are no character assets here and commissioning some is not the point,
 * so these are SVG figures with a CSS walk cycle. Stylised on purpose: a flat
 * figure that moves convincingly reads as art direction, while a detailed one
 * that moves badly reads as broken.
 *
 * `phase` drives the body — walking runs the cycle, talking adds a breathing
 * bob, idle stands still. `speaking` drives the face independently, because the
 * mouth has to follow the audio while the body is doing something else.
 *
 * The blink is on a long, offset loop per character. It is the cheapest thing
 * in the file and does more for "this is a person" than anything else here.
 */
export default function Character({
  variant = "learner", phase = "idle", flip = false, speaking = false, mood = "neutral",
}) {
  const learner = variant === "learner";
  const palette = learner
    ? { body: "#f0902c", shirt: "#12a25f", skin: "#e0a878", hair: "#241a14" }
    : { body: "#3f6fe0", shirt: "#ffd23f", skin: "#f0c9a0", hair: "#4a3220" };

  return (
    <div className={`figure figure--${phase} ${flip ? "figure--flip" : ""}`}>
      <svg viewBox="0 0 64 150" width="100%" height="100%" aria-hidden="true">
        <defs>
          <clipPath id={`face-${variant}`}><circle cx="32" cy="24" r="13" /></clipPath>
        </defs>

        {/* back limbs, drawn first so the front pair overlaps them */}
        <g className="limb limb--legBack" style={{ transformOrigin: "27px 86px" }}>
          <rect x="22" y="84" width="11" height="52" rx="5.5" fill={palette.body} opacity=".5" />
          <rect x="18" y="130" width="18" height="9" rx="4.5" fill="#0d0d0d" opacity=".6" />
        </g>
        <g className="limb limb--armBack" style={{ transformOrigin: "24px 52px" }}>
          <rect x="20" y="50" width="8" height="40" rx="4" fill={palette.body} opacity=".5" />
        </g>

        <path d="M20 50c0-7 5-12 12-12s12 5 12 12v30c0 6-5 10-12 10s-12-4-12-10z"
              fill={palette.body} />
        <rect x="20" y="72" width="24" height="18" rx="6" fill={palette.shirt} opacity=".92" />
        <rect x="29" y="32" width="6" height="10" fill={palette.skin} />

        {/* head */}
        <circle cx="32" cy="24" r="13" fill={palette.skin} />
        <g clipPath={`url(#face-${variant})`}>
          <path d={learner
            ? "M19 20a13 13 0 0 1 26 0c0 3-3-5-13-5s-13 8-13 5z"
            : "M18 22a14 14 0 0 1 28 0c0 4-2-2-5 2-2-5-6-6-9-6s-7 1-9 6c-3-4-5 2-5-2z"}
            fill={palette.hair} />
        </g>

        {/* eyebrows carry the mood; nothing else about the face needs to move */}
        <g className="brows" fill="none" stroke={palette.hair} strokeWidth="1.6"
           strokeLinecap="round">
          <path d={mood === "puzzled" ? "M25 18.5l5-1.6" : "M25 18l5-.6"} />
          <path d={mood === "puzzled" ? "M39 17.5l-5-.6" : "M39 18l-5-.6"} />
        </g>

        <g className="eyes">
          <ellipse className="eye" cx="27" cy="23" rx="1.9" ry="2.3" fill="#1b1410" />
          <ellipse className="eye" cx="37" cy="23" rx="1.9" ry="2.3" fill="#1b1410" />
        </g>

        {/* mouth: a closed line that opens into an ellipse while speaking */}
        <g className={`mouth ${speaking ? "is-speaking" : ""}`}>
          <ellipse className="mouth__open" cx="32" cy="30.5" rx="3.4" ry="2.6" fill="#5c2a26" />
          <path className="mouth__shut" d="M28.6 30.4q3.4 2 6.8 0" fill="none"
                stroke="#5c2a26" strokeWidth="1.5" strokeLinecap="round" />
        </g>

        {/* front limbs */}
        <g className="limb limb--legFront" style={{ transformOrigin: "37px 86px" }}>
          <rect x="32" y="84" width="11" height="52" rx="5.5" fill={palette.body} />
          <rect x="28" y="130" width="18" height="9" rx="4.5" fill="#1a1a1a" />
        </g>
        <g className="limb limb--armFront" style={{ transformOrigin: "40px 52px" }}>
          <rect x="36" y="50" width="8" height="40" rx="4" fill={palette.body} />
        </g>
      </svg>
    </div>
  );
}
