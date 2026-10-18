import { useEffect, useState } from "react";
import { AbsoluteFill, cancelRender, continueRender, delayRender, useVideoConfig } from "remotion";
import { resolveTokens, Tokens } from "./tokens";

export type SayWhyIntroProps = {
  // Frame size. Read in calculateMetadata, so `--props` can change them.
  width?: number;
  height?: number;
  // Token values by token name; see tokens.ts and production/design-tokens.md.
  tokens?: Tokens;
};

export const INTRO_WIDTH = 3840;
export const INTRO_HEIGHT = 2160;

const WORDS = "Say Why";

// The "Intro layout (say-why-intro)" fractions in production/design-tokens.md.
const CAP_HEIGHT = 0.09; // of frame height
const CENTER_Y = 0.46; // of frame height; centre of the cap-height band
const RULE_WIDTH = 1 / 6; // of frame width
const RULE_THICKNESS = 0.004; // of frame height
const RULE_GAP = 0.05; // of frame height, baseline to rule top

// Cap height as a fraction of the font size, measured from the glyph "H" in
// the resolved font. Fonts differ (Helvetica 0.72, others 0.65 to 0.75), so the
// font size is derived from this and not guessed.
const MEASURE_PX = 1000;
const measureCapRatio = (family: string, weight: number): number => {
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx) throw new Error("No 2d canvas to measure the font");
  ctx.font = `${weight} ${MEASURE_PX}px ${family}`;
  return ctx.measureText("H").actualBoundingBoxAscent / MEASURE_PX;
};

const WEIGHT = 700;

export const SayWhyIntro: React.FC<SayWhyIntroProps> = ({ tokens }) => {
  const { width, height } = useVideoConfig();
  const t = resolveTokens(tokens);
  const family = t["type.display"];
  const [ratio, setRatio] = useState<number | null>(null);

  useEffect(() => {
    const handle = delayRender("Measuring the display font");
    document.fonts
      .load(`${WEIGHT} ${MEASURE_PX}px ${family}`, WORDS)
      .then(() => {
        setRatio(measureCapRatio(family, WEIGHT));
        continueRender(handle);
      })
      .catch((e) => cancelRender(e));
  }, [family]);

  const capPx = CAP_HEIGHT * height;
  // Cap-height band centred at CENTER_Y; the baseline is its lower edge.
  const baseline = CENTER_Y * height + capPx / 2;
  const ruleW = RULE_WIDTH * width;

  return (
    <AbsoluteFill style={{ backgroundColor: t["color.marrow"] }}>
      {ratio !== null && (
        <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
          <text
            x={width / 2}
            y={baseline}
            textAnchor="middle"
            fontFamily={family}
            fontWeight={WEIGHT}
            fontSize={capPx / ratio}
            fill={t["color.bone"]}
          >
            {WORDS}
          </text>
          <rect
            x={(width - ruleW) / 2}
            y={baseline + RULE_GAP * height}
            width={ruleW}
            height={RULE_THICKNESS * height}
            fill={t["color.amber"]}
          />
        </svg>
      )}
    </AbsoluteFill>
  );
};
