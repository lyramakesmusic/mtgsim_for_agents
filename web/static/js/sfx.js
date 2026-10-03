// Procedural sound effects (WebAudio). sfx(name, seat?, amount?)
import { S } from "./util.js";

let ctx = null, master = null, noiseBuf = null, verb = null;
const lastAt = {};
const SEAT_ROOT = { P1: 0, P2: 3, P3: 7, P4: 10 };     // semitone offsets so seats sound distinct

function init() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  ctx = new AC();
  master = ctx.createGain();
  master.gain.value = S.sound;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -18; comp.ratio.value = 4;
  master.connect(comp).connect(ctx.destination);
  noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 1.5, ctx.sampleRate);
  const d = noiseBuf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  // short synthetic room
  verb = ctx.createConvolver();
  const ir = ctx.createBuffer(2, ctx.sampleRate * 1.2, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const ch = ir.getChannelData(c);
    for (let i = 0; i < ch.length; i++) ch[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / ch.length, 3.2);
  }
  verb.buffer = ir;
  const vg = ctx.createGain(); vg.gain.value = 0.22;
  verb.connect(vg).connect(master);
  return ctx;
}

export function unlockAudio() {
  const c = init();
  if (c && c.state === "suspended") c.resume();
}
export function setVolume(v) { if (master) master.gain.setTargetAtTime(v, ctx.currentTime, 0.05); }

const hz = (semi) => 220 * Math.pow(2, semi / 12);

function tone(t, freq, dur, { type = "sine", gain = 0.2, attack = 0.005, to = null, wet = 0.3 } = {}) {
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t);
  if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(gain, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.connect(g); g.connect(master);
  if (wet) { const w = ctx.createGain(); w.gain.value = wet; g.connect(w).connect(verb); }
  o.start(t); o.stop(t + dur + 0.05);
}

function noise(t, dur, { gain = 0.2, type = "bandpass", f = 2000, f2 = null, q = 1, attack = 0.004, wet = 0.2 } = {}) {
  const s = ctx.createBufferSource(); s.buffer = noiseBuf;
  const fl = ctx.createBiquadFilter(); fl.type = type; fl.frequency.setValueAtTime(f, t); fl.Q.value = q;
  if (f2) fl.frequency.exponentialRampToValueAtTime(f2, t + dur);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(gain, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  s.connect(fl).connect(g).connect(master);
  if (wet) { const w = ctx.createGain(); w.gain.value = wet; g.connect(w).connect(verb); }
  s.start(t, Math.random() * 0.5); s.stop(t + dur + 0.05);
}

const SOUNDS = {
  draw: (t) => noise(t, 0.09, { f: 3800, f2: 1600, q: 0.8, gain: 0.12, wet: 0.05 }),
  land: (t) => { tone(t, 110, 0.22, { to: 48, gain: 0.35, wet: 0.1 }); noise(t, 0.05, { f: 900, gain: 0.08, wet: 0 }); },
  cast: (t, r) => { tone(t, hz(r + 12), 0.7, { type: "triangle", gain: 0.08 }); tone(t + 0.06, hz(r + 19), 0.8, { type: "sine", gain: 0.06 });
    noise(t, 0.35, { f: 5000, f2: 9000, q: 3, gain: 0.03 }); },
  resolve: (t, r) => { noise(t, 0.28, { type: "lowpass", f: 400, f2: 3000, gain: 0.1 }); tone(t + 0.2, 92, 0.2, { to: 55, gain: 0.26, wet: 0.1 });
    tone(t + 0.02, hz(r + 24), 0.4, { gain: 0.03 }); },
  token: (t, r) => { tone(t, hz(r + 24), 0.16, { type: "triangle", gain: 0.07, wet: 0.2 }); tone(t + 0.05, hz(r + 31), 0.2, { type: "triangle", gain: 0.05 }); },
  die: (t) => { noise(t, 0.45, { type: "lowpass", f: 2400, f2: 200, gain: 0.12 }); tone(t, 180, 0.4, { to: 60, gain: 0.12, type: "triangle" }); },
  damage: (t, r, n = 1) => { const g = Math.min(0.5, 0.14 + n * 0.025);
    tone(t, 140, 0.3, { to: 38, gain: g, wet: 0.15 }); noise(t, 0.12, { type: "lowpass", f: 1800, f2: 300, gain: g * 0.6 }); },
  gain: (t, r) => { tone(t, hz(r + 12), 0.4, { gain: 0.06 }); tone(t + 0.09, hz(r + 16), 0.45, { gain: 0.06 }); tone(t + 0.18, hz(r + 19), 0.6, { gain: 0.05 }); },
  turn: (t, r) => { for (const [m, g] of [[1, 0.06], [2.76, 0.025], [5.4, 0.012]]) tone(t, hz(r + 7) * m, 1.6, { gain: g, wet: 0.5 }); },
  attack: (t) => { noise(t, 0.5, { type: "bandpass", f: 300, f2: 1400, q: 0.7, gain: 0.16 }); tone(t, 70, 0.5, { gain: 0.18 }); },
  block: (t) => { tone(t, 420, 0.08, { type: "square", gain: 0.04, wet: 0.2 }); noise(t, 0.06, { f: 1200, q: 4, gain: 0.12 }); },
  combat: (t) => { tone(t, 120, 0.5, { to: 35, gain: 0.35, wet: 0.3 }); noise(t, 0.3, { type: "lowpass", f: 2600, f2: 180, gain: 0.2 }); },
  counter: (t) => { tone(t, 1400, 0.5, { to: 180, type: "sawtooth", gain: 0.035, wet: 0.4 }); noise(t, 0.25, { f: 6000, q: 6, gain: 0.05 }); },
  reveal: (t) => { for (let i = 0; i < 4; i++) tone(t + i * 0.05, hz(24 + i * 5), 0.5, { gain: 0.03, wet: 0.6 }); },
  coin: (t) => { tone(t, 2600, 0.5, { gain: 0.05, wet: 0.5 }); tone(t + 0.002, 3900, 0.35, { gain: 0.03 }); },
  judge: (t) => { for (const d of [0, 0.16]) { tone(t + d, 160, 0.12, { type: "triangle", gain: 0.25, wet: 0.3 }); noise(t + d, 0.04, { f: 800, gain: 0.15 }); } },
  elim: (t) => { tone(t, 60, 2.4, { to: 28, gain: 0.5, wet: 0.6 }); noise(t, 1.6, { type: "lowpass", f: 900, f2: 60, gain: 0.3, wet: 0.6 }); },
  victory: (t) => { [0, 4, 7, 12, 16].forEach((s, i) => tone(t + i * 0.07, hz(s + 3), 2.6, { type: "triangle", gain: 0.05, attack: 0.08, wet: 0.7 })); },
  talk: () => {},
  ui: (t) => tone(t, 1200, 0.05, { gain: 0.02, wet: 0 }),
  tick: (t) => { noise(t, 0.018, { type: "highpass", f: 3500, gain: 0.09, wet: 0 }); tone(t, 2200, 0.03, { type: "triangle", gain: 0.02, wet: 0 }); },
};

export function sfx(name, seat, amount) {
  if (!S.sound || !ctx || ctx.state !== "running") return;
  const now = performance.now();
  if (now - (lastAt[name] || 0) < (name === "tick" ? 16 : 45)) return;   // mass events: one voice, not forty
  lastAt[name] = now;
  const f = SOUNDS[name];
  if (f) f(ctx.currentTime + 0.01, SEAT_ROOT[seat] ?? 0, amount);
}
