import React from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const zhFontFamily =
  '"Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif';

type BriefingItem = {
  itemIndex: number;
  title: string;
  pubdate: string;
  summary: string;
  relatedField: string;
  importance: "高" | "中" | "低" | string;
  reserveReason: string;
  link: string;
  voiceover: string;
  audioSrc: string;
  duration: number;
};

export type BriefingProps = {
  title: string;
  date: string;
  introVoiceover?: string;
  introAudioSrc?: string;
  introDuration?: number;
  outroVoiceover?: string;
  outroAudioSrc?: string;
  outroDuration?: number;
  fps?: number;
  width?: number;
  height?: number;
  items: BriefingItem[];
};

export const defaultBriefingProps: BriefingProps = {
  title: "游戏行业新闻精选",
  date: "2026-06-17",
  introVoiceover: "大家好，欢迎收看今天的游戏行业日报。",
  introAudioSrc: "",
  introDuration: 3,
  outroVoiceover: "以上就是今天的游戏行业日报，感谢收看，我们下期再见。",
  outroAudioSrc: "",
  outroDuration: 3,
  fps: 30,
  width: 1920,
  height: 1080,
  items: [],
};

const INTRO_SECONDS = 3;
const OUTRO_SECONDS = 3;
const MIN_NEWS_SECONDS = 8;

export const briefingDurationInFrames = (props: BriefingProps): number => {
  const fps = props.fps ?? 30;
  const introSeconds = Math.max(INTRO_SECONDS, Math.ceil((props.introDuration ?? 0) + 1));
  const outroSeconds = Math.max(OUTRO_SECONDS, Math.ceil((props.outroDuration ?? 0) + 1));
  const itemSeconds = (props.items ?? []).reduce((sum, item) => {
    return sum + Math.max(MIN_NEWS_SECONDS, Math.ceil((item.duration ?? 0) + 1));
  }, 0);
  return Math.max(1, Math.ceil((introSeconds + itemSeconds + outroSeconds) * fps));
};

export const DailyBriefing: React.FC<BriefingProps> = (props) => {
  const fps = props.fps ?? 30;
  let cursor = 0;

  const introFrames = Math.max(INTRO_SECONDS, Math.ceil((props.introDuration ?? 0) + 1)) * fps;
  const outroFrames = Math.max(OUTRO_SECONDS, Math.ceil((props.outroDuration ?? 0) + 1)) * fps;
  const progressItems = props.items ?? [];

  const sequences: React.ReactNode[] = [];
  sequences.push(
    <Sequence key="intro" from={cursor} durationInFrames={introFrames}>
      <IntroScene
        title={props.title}
        date={props.date}
        progressItems={progressItems}
        voiceover={props.introVoiceover ?? ""}
        audioSrc={props.introAudioSrc ?? ""}
      />
    </Sequence>,
  );
  cursor += introFrames;

  props.items.forEach((item, index) => {
    const duration = Math.max(MIN_NEWS_SECONDS, Math.ceil((item.duration ?? 0) + 1));
    const frames = duration * fps;
    sequences.push(
      <Sequence key={`item-${item.itemIndex}`} from={cursor} durationInFrames={frames}>
        <NewsScene item={item} progressItems={progressItems} activeIndex={index} />
      </Sequence>,
    );
    cursor += frames;
  });

  sequences.push(
    <Sequence key="outro" from={cursor} durationInFrames={outroFrames}>
      <OutroScene
        title={props.title}
        progressItems={progressItems}
        voiceover={props.outroVoiceover ?? ""}
        audioSrc={props.outroAudioSrc ?? ""}
      />
    </Sequence>,
  );

  return (
    <AbsoluteFill style={styles.root}>
      {sequences}
    </AbsoluteFill>
  );
};

const IntroScene: React.FC<{
  title: string;
  date: string;
  progressItems: BriefingItem[];
  voiceover: string;
  audioSrc: string;
}> = ({ title, date, progressItems, voiceover, audioSrc }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const opacity = interpolate(frame, [0, 18], [0, 1], { extrapolateRight: "clamp" });
  return (
    <Screen>
      <TopProgress items={progressItems} />
      {audioSrc ? <Audio src={staticFile(audioSrc)} /> : null}
      <div style={{ ...styles.center, opacity }}>
        <div style={styles.kicker}>DAILY GAME BRIEFING</div>
        <h1 style={styles.heroTitle}>{title}</h1>
        <div style={styles.date}>{date}</div>
      </div>
      <Subtitle text={currentSubtitle(voiceover, frame, fps)} />
    </Screen>
  );
};

const NewsScene: React.FC<{ item: BriefingItem; progressItems: BriefingItem[]; activeIndex: number }> = ({
  item,
  progressItems,
  activeIndex,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const opacity = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  const y = interpolate(frame, [0, 18], [20, 0], { extrapolateRight: "clamp" });
  const summaryLength = item.summary.length;
  const summaryTextStyle = {
    ...styles.cardText,
    ...(summaryLength > 360 ? styles.compactSummaryText : summaryLength > 220 ? styles.mediumSummaryText : {}),
  };

  return (
    <Screen>
      <TopProgress items={progressItems} activeIndex={activeIndex} />
      {item.audioSrc ? <Audio src={staticFile(item.audioSrc)} /> : null}
      <h1 style={styles.sceneTitle}>{item.title}</h1>
      <div style={{ ...styles.newsGrid, opacity, transform: `translateY(${y}px)` }}>
        <InfoCard title="摘要" accent="#cf6844" style={styles.summaryCard} textStyle={summaryTextStyle}>
          {item.summary}
        </InfoCard>
        <InfoCard title="方向" accent="#d89b34">
          {item.relatedField || "游戏行业"}
        </InfoCard>
        <InfoCard title="看点" accent="#3f6f76">
          {item.reserveReason || "该新闻与游戏行业动态相关。"}
        </InfoCard>
      </div>
      <Subtitle text={currentSubtitle(item.voiceover, frame, fps)} />
    </Screen>
  );
};

const OutroScene: React.FC<{
  title: string;
  progressItems: BriefingItem[];
  voiceover: string;
  audioSrc: string;
}> = ({ title, progressItems, voiceover, audioSrc }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <Screen>
      <TopProgress items={progressItems} />
      {audioSrc ? <Audio src={staticFile(audioSrc)} /> : null}
      <div style={styles.center}>
        <div style={styles.kicker}>THANKS FOR WATCHING</div>
        <h1 style={styles.heroTitle}>{title}</h1>
        <div style={styles.date}>明天继续关注游戏行业新动态</div>
      </div>
      <Subtitle text={currentSubtitle(voiceover, frame, fps)} />
    </Screen>
  );
};

const Screen: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill style={styles.screen}>
    <div style={styles.backgroundGlow} />
    {children}
  </AbsoluteFill>
);

const TopProgress: React.FC<{ items: BriefingItem[]; activeIndex?: number }> = ({ items, activeIndex }) => {
  const segments =
    items.length > 0
      ? items
      : [
          {
            itemIndex: 1,
            title: "游戏早报",
            summary: "游戏行业新闻精选",
            pubdate: "",
            relatedField: "",
            importance: "中",
            reserveReason: "",
            link: "",
            voiceover: "",
            audioSrc: "",
            duration: 0,
          },
        ];
  return (
    <div style={styles.topbar}>
      {segments.map((item, index) => (
        <div
          key={`${item.itemIndex}-${index}`}
          style={{ ...styles.progressSegment, ...(index === activeIndex ? styles.activeProgressSegment : {}) }}
        >
          <span style={styles.progressText}>{item.summary || item.title || "新闻摘要"}</span>
        </div>
      ))}
    </div>
  );
};

const InfoCard: React.FC<{
  title: string;
  accent: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
  textStyle?: React.CSSProperties;
}> = ({
  title,
  accent,
  children,
  style,
  textStyle,
}) => (
  <div style={{ ...styles.card, ...style }}>
    <h2 style={{ ...styles.cardTitle, color: accent }}>{title}</h2>
    <p style={{ ...styles.cardText, ...textStyle }}>{children}</p>
  </div>
);

const Subtitle: React.FC<{ text: string }> = ({ text }) => {
  if (!text) return null;
  return <div style={styles.subtitle}>{text}</div>;
};

const currentSubtitle = (text: string, frame: number, fps: number): string => {
  if (!text) return "";
  const chunks = text
    .split(/[。！？!?；;]/)
    .map((part) => part.trim())
    .filter(Boolean);
  if (chunks.length === 0) return text;
  const index = Math.min(chunks.length - 1, Math.floor(frame / (fps * 3)));
  return chunks[index];
};

const styles: Record<string, React.CSSProperties> = {
  root: {
    backgroundColor: "#f7f7ef",
    fontFamily: zhFontFamily,
  },
  screen: {
    backgroundColor: "#f7f7ef",
    color: "#1f2933",
    overflow: "hidden",
  },
  backgroundGlow: {
    position: "absolute",
    inset: "120px 160px",
    background: "radial-gradient(circle at 50% 20%, rgba(207,104,68,0.12), transparent 60%)",
  },
  topbar: {
    height: 86,
    display: "flex",
    background: "#efefe4",
    borderBottom: "1px solid #d8d8c8",
    zIndex: 2,
  },
  progressSegment: {
    flex: 1,
    minWidth: 0,
    display: "flex",
    alignItems: "center",
    padding: "12px 18px",
    borderRight: "1px solid #d8d8c8",
    color: "#2c3138",
    overflow: "hidden",
  },
  activeProgressSegment: {
    background: "#cf6844",
    color: "#fff",
  },
  progressText: {
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    display: "-webkit-box",
    WebkitBoxOrient: "vertical",
    WebkitLineClamp: 2,
    fontSize: 17,
    fontWeight: 800,
    lineHeight: 1.25,
  },
  center: {
    position: "absolute",
    inset: 0,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
  },
  kicker: {
    fontSize: 30,
    color: "#9b3e2a",
    letterSpacing: 0,
    marginBottom: 24,
    fontWeight: 800,
  },
  heroTitle: {
    margin: 0,
    color: "#cf6844",
    fontSize: 82,
    fontWeight: 900,
  },
  date: {
    marginTop: 28,
    color: "#4b5563",
    fontSize: 32,
  },
  sceneTitle: {
    margin: "120px auto 0",
    width: 1500,
    textAlign: "center",
    color: "#cf6844",
    fontSize: 60,
    fontWeight: 900,
    lineHeight: 1.15,
  },
  newsGrid: {
    margin: "52px auto 0",
    width: 1620,
    display: "grid",
    gridTemplateColumns: "1.45fr 1fr",
    gap: 32,
  },
  card: {
    height: 250,
    background: "#fff",
    borderRadius: 24,
    padding: "34px 40px",
    boxShadow: "0 10px 28px rgba(0,0,0,0.08)",
    overflow: "hidden",
  },
  cardTitle: {
    margin: "0 0 24px",
    fontSize: 40,
    fontWeight: 900,
  },
  cardText: {
    margin: 0,
    color: "#151b23",
    fontSize: 30,
    lineHeight: 1.45,
    overflowWrap: "anywhere",
    wordBreak: "break-word",
    display: "-webkit-box",
    WebkitBoxOrient: "vertical",
    WebkitLineClamp: 8,
    overflow: "hidden",
  },
  summaryCard: {
    gridRow: "span 2",
    minHeight: 532,
  },
  mediumSummaryText: {
    fontSize: 27,
    lineHeight: 1.42,
    WebkitLineClamp: 12,
  },
  compactSummaryText: {
    fontSize: 24,
    lineHeight: 1.38,
    WebkitLineClamp: 15,
  },
  subtitle: {
    position: "absolute",
    left: "50%",
    bottom: 72,
    transform: "translateX(-50%)",
    maxWidth: 1280,
    padding: "16px 34px",
    background: "rgba(0,0,0,0.56)",
    color: "#fff",
    fontSize: 34,
    borderRadius: 8,
    textAlign: "center",
    lineHeight: 1.25,
    overflowWrap: "anywhere",
    wordBreak: "break-word",
  },
};
