const clamp = (value: number, min = 0, max = 1) => Math.max(min, Math.min(max, value));

const GREEN: [number, number, number] = [0, 255, 135];
const AMBER: [number, number, number] = [255, 184, 0];
const RED: [number, number, number] = [255, 0, 64];

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

const rgbToHex = (rgb: [number, number, number]) =>
  `#${rgb
    .map((value) => Math.round(value).toString(16).padStart(2, "0"))
    .join("")}`;

export const decisionPalette = {
  PASS: ["#00ff87", "#00d97e"],
  ESCALATE: ["#ffb800", "#ff8c00"],
  PAUSE: ["#ff3366", "#ff0040"],
} as const;

export const lifecyclePalette: Record<string, string> = {
  ACTIVE: "#00ff87",
  DEGRADED: "#ff8c00",
  RECOVERING: "#ffb800",
  PAUSED: "#ff0040",
  UNKNOWN: "#52525b",
};

export function riskToRgb(score: number): [number, number, number] {
  const normalized = clamp(score);
  if (normalized <= 0.5) {
    const t = normalized / 0.5;
    return [
      lerp(GREEN[0], AMBER[0], t),
      lerp(GREEN[1], AMBER[1], t),
      lerp(GREEN[2], AMBER[2], t),
    ];
  }

  const t = (normalized - 0.5) / 0.5;
  return [
    lerp(AMBER[0], RED[0], t),
    lerp(AMBER[1], RED[1], t),
    lerp(AMBER[2], RED[2], t),
  ];
}

export function riskToHex(score: number): string {
  return rgbToHex(riskToRgb(score));
}

export function riskGradientStyle(score: number): { background: string } {
  const start = riskToHex(score);
  const end = riskToHex(clamp(score + 0.2));
  return {
    background: `linear-gradient(90deg, ${start}, ${end})`,
  };
}

export function formatDelta(value: number): string {
  const rounded = Number(value.toFixed(2));
  if (rounded > 0) {
    return `+${rounded}`;
  }
  return `${rounded}`;
}
