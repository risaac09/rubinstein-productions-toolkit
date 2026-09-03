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
};

const ENTRANCE_DURATION = 20;
const HOLD_DURATION = 60;
const EXIT_DURATION = 15;
const TOTAL_DURATION = ENTRANCE_DURATION + HOLD_DURATION + EXIT_DURATION;

export const lowerThirdDurationInFrames = TOTAL_DURATION;

export const LowerThird: React.FC<LowerThirdProps> = ({ name, role }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const exitStart = ENTRANCE_DURATION + HOLD_DURATION;

  const entranceProgress = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: ENTRANCE_DURATION,
  });

  // Remotion renders frames 0..durationInFrames - 1, so the exit window's
  // right edge must land on the last rendered frame, not TOTAL_DURATION.
  const exitProgress = interpolate(
    frame,
    [exitStart, TOTAL_DURATION - 1],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.quad) },
  );

  const translateX = interpolate(entranceProgress, [0, 1], [-80, 0]) - exitProgress * 80;
  const opacity = interpolate(entranceProgress, [0, 1], [0, 1]) * (1 - exitProgress);
  const barScale = entranceProgress * (1 - exitProgress);

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          left: 80,
          bottom: 120,
          display: "flex",
          alignItems: "center",
          transform: `translateX(${translateX}px)`,
          opacity,
        }}
      >
        <div
          style={{
            width: 6,
            height: 90,
            backgroundColor: "#e8491d",
            transform: `scaleY(${barScale})`,
            transformOrigin: "bottom",
            marginRight: 24,
          }}
        />
        <div
          style={{
            backgroundColor: "rgba(15, 15, 15, 0.88)",
            padding: "16px 32px",
            borderRadius: 4,
          }}
        >
          <div
            style={{
              fontFamily: "Helvetica, Arial, sans-serif",
              fontSize: 42,
              fontWeight: 700,
              color: "#ffffff",
              lineHeight: 1.1,
            }}
          >
            {name}
          </div>
          <div
            style={{
              fontFamily: "Helvetica, Arial, sans-serif",
              fontSize: 24,
              fontWeight: 400,
              color: "#c9c9c9",
              marginTop: 4,
            }}
          >
            {role}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
