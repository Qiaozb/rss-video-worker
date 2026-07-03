import path from "node:path";
import fs from "node:fs";
import crypto from "node:crypto";
import { bundle } from "@remotion/bundler";
import { renderMedia } from "@remotion/renderer";

const propsPath = process.argv[2];
const outputPath = process.argv[3];

if (!propsPath || !outputPath) {
  console.error("Usage: node remotion/render.mjs <props.json> <output.mp4>");
  process.exit(1);
}

const inputProps = JSON.parse(fs.readFileSync(propsPath, "utf-8"));
const entryPoint = path.resolve("remotion/src/index.tsx");
const publicDir = path.resolve("remotion/public");
const cacheRoot = path.resolve(".cache");
const bundleDir = path.join(cacheRoot, "remotion-bundle");
const bundleHashPath = path.join(cacheRoot, "remotion-bundle.sha256");

const browserCandidates = [
  process.env.REMOTION_BROWSER_EXECUTABLE,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);
const browserExecutable = browserCandidates.find((candidate) =>
  fs.existsSync(candidate),
);
const browserOptions = browserExecutable ? {browserExecutable} : {};
const concurrencySetting = process.env.REMOTION_CONCURRENCY || "2";
const concurrency = /^\d+$/.test(concurrencySetting)
  ? Number(concurrencySetting)
  : concurrencySetting;
const hardwareAcceleration =
  process.env.REMOTION_HARDWARE_ACCELERATION || "disable";
const remotionPort = process.env.REMOTION_PROXY_PORT
  ? Number(process.env.REMOTION_PROXY_PORT)
  : null;

if (!["disable", "if-possible", "required"].includes(hardwareAcceleration)) {
  throw new Error(
    `Invalid REMOTION_HARDWARE_ACCELERATION: ${hardwareAcceleration}`,
  );
}

if (
  remotionPort !== null &&
  (!Number.isInteger(remotionPort) || remotionPort <= 0)
) {
  throw new Error(`Invalid REMOTION_PROXY_PORT: ${process.env.REMOTION_PROXY_PORT}`);
}

console.log(
  `Render settings: concurrency=${concurrency}, hardwareAcceleration=${hardwareAcceleration}`,
);

const collectFiles = (target) => {
  if (!fs.existsSync(target)) return [];
  const stat = fs.statSync(target);
  if (stat.isFile()) return [target];
  return fs
    .readdirSync(target, {withFileTypes: true})
    .flatMap((entry) => collectFiles(path.join(target, entry.name)));
};

const bundleFingerprint = () => {
  const hash = crypto.createHash("sha256");
  const inputs = [
    path.resolve("remotion/src"),
    path.resolve("package.json"),
    path.resolve("package-lock.json"),
    path.resolve("tsconfig.json"),
  ];
  for (const file of inputs.flatMap(collectFiles).sort()) {
    hash.update(path.relative(process.cwd(), file));
    hash.update(fs.readFileSync(file));
  }
  return hash.digest("hex");
};

const fingerprint = bundleFingerprint();
const cachedFingerprint = fs.existsSync(bundleHashPath)
  ? fs.readFileSync(bundleHashPath, "utf-8").trim()
  : "";
const canReuseBundle =
  process.env.REMOTION_REBUILD_BUNDLE !== "1" &&
  cachedFingerprint === fingerprint &&
  fs.existsSync(path.join(bundleDir, "index.html"));

let serveUrl;
if (canReuseBundle) {
  serveUrl = bundleDir;
  console.log(`Reusing Remotion bundle: ${bundleDir}`);
} else {
  fs.mkdirSync(cacheRoot, {recursive: true});
  fs.rmSync(bundleDir, {recursive: true, force: true});
  serveUrl = await bundle({
    entryPoint,
    publicDir,
    outDir: bundleDir,
    symlinkPublicDir: true,
    enableCaching: true,
    onProgress: () => undefined,
  });
  fs.writeFileSync(bundleHashPath, `${fingerprint}\n`, "utf-8");
  console.log(`Created Remotion bundle: ${bundleDir}`);
}
console.log(`Serving Remotion bundle: ${serveUrl}`);
console.log(`Remotion proxy port: ${remotionPort ?? "auto"}`);

const fps = inputProps.fps ?? 30;
const introSeconds = Math.max(3, Math.ceil((inputProps.introDuration ?? 0) + 1));
const outroSeconds = Math.max(3, Math.ceil((inputProps.outroDuration ?? 0) + 1));
const itemSeconds = (inputProps.items ?? []).reduce(
  (sum, item) => sum + Math.max(8, Math.ceil((item.duration ?? 0) + 1)),
  0,
);

const composition = {
  id: "DailyBriefing",
  width: inputProps.width ?? 1920,
  height: inputProps.height ?? 1080,
  fps,
  durationInFrames: Math.max(
    1,
    Math.ceil((introSeconds + itemSeconds + outroSeconds) * fps),
  ),
  defaultProps: {},
  props: inputProps,
  defaultCodec: null,
  defaultOutName: null,
  defaultVideoImageFormat: null,
  defaultPixelFormat: null,
  defaultProResProfile: null,
  defaultSampleRate: null,
};

await renderMedia({
  composition,
  serveUrl,
  codec: "h264",
  outputLocation: outputPath,
  inputProps,
  ...browserOptions,
  ...(remotionPort === null ? {} : {port: remotionPort}),
  concurrency,
  hardwareAcceleration,
  onProgress: (progress) => {
    const payload =
      typeof progress === "number" ? { totalProgress: progress } : progress;
    console.log(`REMOTION_PROGRESS ${JSON.stringify(payload)}`);
  },
  chromiumOptions: {
    enableMultiProcessOnLinux: process.platform === "linux",
  },
});

console.log(`Rendered ${outputPath}`);
