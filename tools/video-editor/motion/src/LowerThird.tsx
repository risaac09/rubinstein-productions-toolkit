import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  Easing,
} from "remotion";

export type LowerThirdProps = {
  name: string;
  role: string;
  // Frame settings. The composition reads these in calculateMetadata, so
  // `--props` can change them at render time. Omitted, they fall back to
  // UHD at 24000/1001 fps.
  width?: number;
  height?: number;
  fps?: number;
  // Token values by token name, from the private brand context
  // (production/design-tokens.md). Any token left out uses the placeholder in
  // FALLBACK below, which is not a brand value.
  tokens?: Partial<Record<TokenName, string>>;
};

export type TokenName =
  | "color.marrow"
  | "color.amber"
  | "color.text-on-dark"
  | "type.display"
  | "type.body";

const FALLBACK: Record<TokenName, string> = {
  "color.marrow": "#0f0f0f",
  "color.amber": "#e8491d",
  "color.text-on-dark": "#ffffff",
  "type.display": "Helvetica, Arial, sans-serif",
  "type.body": "Helvetica, Arial, sans-serif",
};

// The panel is color.marrow at 88% opacity, the same number as the caption bar.
const PANEL_OPACITY = 0.88;

// UHD at 23.976. Remotion accepts any positive finite number for fps, so the
// exact rational 24000/1001 is used and no rounding to 24 is needed.
export const DEFAULT_WIDTH = 3840;
export const DEFAULT_HEIGHT = 2160;
export const DEFAULT_FPS = 24000 / 1001;

// Timing in seconds, so the motion lasts the same at any frame rate. At 30 fps
// these are the old 20, 60 and 15 frames.
const ENTRANCE_SECONDS = 20 / 30;
const HOLD_SECONDS = 60 / 30;
const EXIT_SECONDS = 15 / 30;

export const lowerThirdDurationInFrames = (fps: number): number =>
  Math.round((ENTRANCE_SECONDS + HOLD_SECONDS + EXIT_SECONDS) * fps);

// Geometry as fractions, mirrored in production/design-tokens.md.
// Landscape measures horizontal things against width and vertical things
// against height. Portrait and square measure everything against width, so the
// block keeps its proportions when the frame is narrow.
type Layout = {
  left: number;
  bottom: number;
  barWidth: number;
  barHeight: number;
  gap: number;
  padX: number;
  padY: number;
  radius: number;
  title: number;
  subtitle: number;
  slide: number;
};

const LANDSCAPE = {
  left: 1 / 24, // of width
  bottom: 1 / 9, // of height
  barWidth: 6 / 1920, // of width
  barHeight: 1 / 12, // of height
  gap: 24 / 1920, // of width
  padX: 32 / 1920, // of width
  padY: 16 / 1080, // of height
  radius: 4 / 1080, // of height
  title: 42 / 1080, // of height
  subtitle: 24 / 1080, // of height
  slide: 80 / 1920, // of width
};

// Portrait and square: all fractions of width, except bottom (of height).
const NARROW = {
  left: 1 / 18,
  bottom: 0.26,
  barWidth: 0.004,
  barHeight: 0.07,
  gap: 0.016,
  padX: 0.025,
  padY: 0.012,
  radius: 0.003,
  title: 0.044,
  subtitle: 0.026,
  slide: 0.05,
};

export const layoutFor = (width: number, height: number): Layout => {
  if (width > height) {
    return {
      left: LANDSCAPE.left * width,
      bottom: LANDSCAPE.bottom * height,
      barWidth: LANDSCAPE.barWidth * width,
      barHeight: LANDSCAPE.barHeight * height,
      gap: LANDSCAPE.gap * width,
      padX: LANDSCAPE.padX * width,
      padY: LANDSCAPE.padY * height,
      radius: LANDSCAPE.radius * height,
      title: LANDSCAPE.title * height,
      subtitle: LANDSCAPE.subtitle * height,
      slide: LANDSCAPE.slide * width,
    };
  }
  return {
    left: NARROW.left * width,
    bottom: NARROW.bottom * height,
    barWidth: NARROW.barWidth * width,
    barHeight: NARROW.barHeight * width,
    gap: NARROW.gap * width,
    padX: NARROW.padX * width,
    padY: NARROW.padY * width,
    radius: NARROW.radius * width,
    title: NARROW.title * width,
    subtitle: NARROW.subtitle * width,
    slide: NARROW.slide * width,
  };
};

export const LowerThird: React.FC<LowerThirdProps> = ({ name, role, tokens }) => {
  const frame = useCurrentFrame();
  // A token that is missing, null or empty in the props JSON keeps its fallback.
  const t = { ...FALLBACK };
  for (const [k, v] of Object.entries(tokens ?? {})) {
    if (k in FALLBACK && typeof v === "string" && v !== "") t[k as TokenName] = v;
  }
  const { fps, width, height, durationInFrames } = useVideoConfig();
  const g = layoutFor(width, height);

  const entranceFrames = Math.round(ENTRANCE_SECONDS * fps);
  const exitStart = durationInFrames - Math.round(EXIT_SECONDS * fps);

  const entranceProgress = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: entranceFrames,
  });

  // Remotion renders frames 0..durationInFrames - 1, so the exit window's
  // right edge must land on the last rendered frame, not durationInFrames.
  const exitProgress = interpolate(
    frame,
    [exitStart, durationInFrames - 1],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.quad) },
  );

  const translateX = interpolate(entranceProgress, [0, 1], [-g.slide, 0]) - exitProgress * g.slide;
  const opacity = interpolate(entranceProgress, [0, 1], [0, 1]) * (1 - exitProgress);
  const barScale = entranceProgress * (1 - exitProgress);

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          left: g.left,
          bottom: g.bottom,
          display: "flex",
          alignItems: "center",
          transform: `translateX(${translateX}px)`,
          opacity,
        }}
      >
        <div
          style={{
            width: g.barWidth,
            height: g.barHeight,
            backgroundColor: t["color.amber"],
            transform: `scaleY(${barScale})`,
            transformOrigin: "bottom",
            marginRight: g.gap,
          }}
        />
        <div
          style={{
            backgroundColor: `color-mix(in srgb, ${t["color.marrow"]} ${PANEL_OPACITY * 100}%, transparent)`,
            padding: `${g.padY}px ${g.padX}px`,
            borderRadius: g.radius,
          }}
        >
          <div
            style={{
              fontFamily: t["type.display"],
              fontSize: g.title,
              fontWeight: 700,
              color: t["color.text-on-dark"],
              lineHeight: 1.1,
            }}
          >
            {name}
          </div>
          <div
            style={{
              fontFamily: t["type.body"],
              fontSize: g.subtitle,
              fontWeight: 400,
              color: t["color.text-on-dark"],
              marginTop: g.subtitle / 6,
            }}
          >
            {role}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
