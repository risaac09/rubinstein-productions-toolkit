import "./index.css";
import { Composition } from "remotion";
import { LowerThird, lowerThirdDurationInFrames } from "./LowerThird";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="LowerThird"
        component={LowerThird}
        durationInFrames={lowerThirdDurationInFrames}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          name: "Isaac Rubinstein",
          role: "Facilitator / Filmmaker",
        }}
      />
    </>
  );
};
