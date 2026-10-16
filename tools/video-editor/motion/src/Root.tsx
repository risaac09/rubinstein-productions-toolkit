import "./index.css";
import { Composition, CalculateMetadataFunction } from "remotion";
import {
  DEFAULT_FPS,
  DEFAULT_HEIGHT,
  DEFAULT_WIDTH,
  LowerThird,
  LowerThirdProps,
  lowerThirdDurationInFrames,
} from "./LowerThird";

const defaultProps: LowerThirdProps = {
  name: "Isaac Rubinstein",
  role: "Facilitator / Filmmaker",
};

// Frame size and rate come from props when given, else from the
// composition's own defaults. Duration follows the frame rate.
const metadata =
  (width: number, height: number): CalculateMetadataFunction<LowerThirdProps> =>
  ({ props }) => {
    const fps = props.fps ?? DEFAULT_FPS;
    return {
      width: props.width ?? width,
      height: props.height ?? height,
      fps,
      durationInFrames: lowerThirdDurationInFrames(fps),
    };
  };

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="LowerThird"
        component={LowerThird}
        durationInFrames={lowerThirdDurationInFrames(DEFAULT_FPS)}
        fps={DEFAULT_FPS}
        width={DEFAULT_WIDTH}
        height={DEFAULT_HEIGHT}
        defaultProps={defaultProps}
        calculateMetadata={metadata(DEFAULT_WIDTH, DEFAULT_HEIGHT)}
      />
      <Composition
        id="LowerThirdPortrait"
        component={LowerThird}
        durationInFrames={lowerThirdDurationInFrames(DEFAULT_FPS)}
        fps={DEFAULT_FPS}
        width={2160}
        height={3840}
        defaultProps={defaultProps}
        calculateMetadata={metadata(2160, 3840)}
      />
      <Composition
        id="LowerThirdSquare"
        component={LowerThird}
        durationInFrames={lowerThirdDurationInFrames(DEFAULT_FPS)}
        fps={DEFAULT_FPS}
        width={2160}
        height={2160}
        defaultProps={defaultProps}
        calculateMetadata={metadata(2160, 2160)}
      />
    </>
  );
};
