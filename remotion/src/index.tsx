import React from "react";
import { Composition, registerRoot } from "remotion";
import { DailyBriefing, briefingDurationInFrames, defaultBriefingProps } from "./DailyBriefing";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="DailyBriefing"
      component={DailyBriefing}
      durationInFrames={briefingDurationInFrames(defaultBriefingProps)}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaultBriefingProps}
      calculateMetadata={({ props }) => {
        return {
          durationInFrames: briefingDurationInFrames(props),
          fps: props.fps ?? 30,
          width: props.width ?? 1920,
          height: props.height ?? 1080,
        };
      }}
    />
  );
};

registerRoot(RemotionRoot);
