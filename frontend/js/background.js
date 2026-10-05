/**
 * Animated background for the home page hero: a slowly twisting DNA double helix runs diagonally behind the search
 * card while small drug molecules drift around it. A molecule that comes close to the helix "binds" (a dashed line
 * to the nearest base pair, and the molecule glows), a nod to what the site explores.
 *
 * The animation pauses when the tab is hidden or the hero is scrolled out of view, and draws a single still frame
 * when the user prefers reduced motion.
 */
import { cssVar } from "./util.js";

const canvas = document.getElementById("hero-bg");
const hero = canvas?.parentElement;
const ctx = canvas?.getContext("2d");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

const BASES = "ACGT";
const PAIR = { A: "T", T: "A", C: "G", G: "C" };
const BASE_COLORS = { A: "16, 150, 110", T: "220, 70, 60", C: "37, 99, 235", G: "142, 68, 173" }; // RGB
const BP_PER_TURN = 10.5;
const STRAND_OFFSET = Math.PI * 0.8;  // the second strand trails the first, leaving a major and a minor groove
const TWIST_SPEED = 0.35;             // radians per second
const BIND_DISTANCE = 70;             // px from a molecule to a base pair before it binds
const POINTER_GLOW = 130;             // px; base pairs near the pointer brighten

const sequence = Array.from({ length: 512 }, () => BASES[Math.floor(Math.random() * 4)]);
let width = 0;
let height = 0;
let helix = null;
let molecules = [];
let pointer = null;
let colors = {};
let running = false;
let visible = true;
let frame = 0;
let lastTime = 0;
let phase = 0;
let clock = 0;

const random = (min, max) => min + Math.random() * (max - min);
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function makeMolecule() {
  return {
    x: random(0, width),
    y: random(0, height),
    vx: random(-0.25, 0.25),
    vy: random(-0.18, 0.18),
    angle: random(0, Math.PI * 2),
    spin: random(-0.004, 0.004),
    size: random(8, 15),
    rings: Math.random() < 0.35 ? 2 : 1,            // single ring (benzene-like) or fused pair (naphthalene-like)
    substituents: Math.floor(random(0, 3)),
    alpha: random(0.3, 0.55),
    glow: 0,
  };
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  width = hero.clientWidth;
  height = hero.clientHeight;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Bottom-left to top-right, overshooting the edges so the ends are never visible.
  const start = { x: -0.08 * width, y: 0.9 * height };
  const end = { x: 1.08 * width, y: 0.1 * height };
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  const radius = clamp(height * 0.075, 26, 56);
  helix = { start, length, ux: dx / length, uy: dy / length, nx: -dy / length, ny: dx / length, radius, rise: radius * 0.42 };

  const target = clamp(Math.round((width * height) / 90000), 6, 16);
  while (molecules.length < target) molecules.push(makeMolecule());
  molecules.length = target;

  colors = { line: cssVar("--hero-node") || "27, 42, 65", accent: cssVar("--hero-accent") || "217, 72, 15" };
  if (!running) draw();
}

/** A point on one strand: along the (gently waving) axis, offset across it by the strand's rotation angle. */
function strandPoint(s, angle) {
  const { start, ux, uy, nx, ny, radius } = helix;
  const wave = radius * 0.9 * Math.sin(s / 520 + clock * 0.12);
  const offset = wave + radius * Math.sin(angle);
  return { x: start.x + ux * s + nx * offset, y: start.y + uy * s + ny * offset, z: Math.cos(angle) };
}

const depth = (z) => (z + 1) / 2; // 0 = back, 1 = front

function helixGeometry() {
  const { length, rise } = helix;
  const twist = (2 * Math.PI) / (BP_PER_TURN * rise);
  const segments = [];
  for (const offset of [0, STRAND_OFFSET]) {
    let previous = null;
    for (let s = 0; s <= length; s += 5) {
      const point = strandPoint(s, s * twist + phase + offset);
      if (previous) segments.push({ a: previous, b: point, z: (previous.z + point.z) / 2 });
      previous = point;
    }
  }
  const rungs = [];
  for (let i = 0, s = rise / 2; s < length; i++, s += rise) {
    const a = strandPoint(s, s * twist + phase);
    const b = strandPoint(s, s * twist + phase + STRAND_OFFSET);
    rungs.push({ a, b, z: (a.z + b.z) / 2, base: sequence[i % sequence.length] });
  }
  return { segments, rungs };
}

function drawSegments(segments) {
  ctx.lineCap = "round";
  for (const { a, b, z } of segments) {
    const d = depth(z);
    ctx.strokeStyle = `rgba(${colors.line}, ${(0.18 + 0.55 * d).toFixed(3)})`;
    ctx.lineWidth = 1.2 + 1.8 * d;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
}

function drawRung({ a, b, z, base }) {
  const near = pointer && Math.hypot((a.x + b.x) / 2 - pointer.x, (a.y + b.y) / 2 - pointer.y) < POINTER_GLOW;
  const alpha = (0.15 + 0.45 * depth(z) + (near ? 0.3 : 0)).toFixed(3);
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  ctx.lineWidth = 2 + 1.5 * depth(z);
  for (const [from, color] of [[a, BASE_COLORS[base]], [b, BASE_COLORS[PAIR[base]]]]) {
    ctx.strokeStyle = `rgba(${color}, ${alpha})`;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(mid.x, mid.y);
    ctx.stroke();
  }
  for (const end of [a, b]) {
    ctx.fillStyle = `rgba(${colors.line}, ${(0.25 + 0.6 * depth(end.z)).toFixed(3)})`;
    ctx.beginPath();
    ctx.arc(end.x, end.y, 1.5 + 2 * depth(end.z), 0, Math.PI * 2);
    ctx.fill();
  }
}

function hexagonPath(cx, cy, size) {
  for (let i = 0; i < 6; i++) {
    const angle = Math.PI / 6 + (i * Math.PI) / 3; // pointy-top, so fused rings share a vertical edge
    const x = cx + size * Math.cos(angle);
    const y = cy + size * Math.sin(angle);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

function drawMolecule(m) {
  const color = m.glow > 0.01 ? colors.accent : colors.line;
  const alpha = Math.min(1, m.alpha + m.glow * 0.5);
  ctx.save();
  ctx.translate(m.x, m.y);
  ctx.rotate(m.angle);
  ctx.strokeStyle = `rgba(${color}, ${alpha.toFixed(3)})`;
  ctx.fillStyle = `rgba(${color}, ${alpha.toFixed(3)})`;
  ctx.lineWidth = 1.4;
  if (m.glow > 0.01) {
    ctx.shadowColor = `rgba(${colors.accent}, ${(m.glow * 0.8).toFixed(3)})`;
    ctx.shadowBlur = 12 * m.glow;
  }

  ctx.beginPath();
  hexagonPath(0, 0, m.size);
  if (m.rings === 2) hexagonPath(m.size * Math.sqrt(3), 0, m.size);
  ctx.stroke();
  ctx.beginPath(); // aromatic ring
  ctx.arc(0, 0, m.size * 0.55, 0, Math.PI * 2);
  ctx.stroke();

  // Side groups: a bond out from a ring vertex ending in an atom.
  for (let i = 0; i < m.substituents; i++) {
    const angle = -Math.PI / 2 + i * (Math.PI * 2 / 3) + Math.PI / 3;
    const from = { x: m.size * Math.cos(angle), y: m.size * Math.sin(angle) };
    const to = { x: from.x * 1.8, y: from.y * 1.8 };
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(to.x, to.y, 2.2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

/** Each molecule binds to the nearest front-facing base pair within reach. */
function drawBindings(rungs) {
  ctx.setLineDash([3, 4]);
  ctx.lineWidth = 1;
  for (const m of molecules) {
    let best = null;
    let bestDistance = BIND_DISTANCE;
    for (const rung of rungs) {
      for (const end of [rung.a, rung.b]) {
        if (end.z < 0.2) continue;
        const d = Math.hypot(end.x - m.x, end.y - m.y);
        if (d < bestDistance) {
          bestDistance = d;
          best = end;
        }
      }
    }
    m.target = best ? 1 : 0;
    if (best) {
      ctx.strokeStyle = `rgba(${colors.accent}, ${(0.7 * (1 - bestDistance / BIND_DISTANCE)).toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(m.x, m.y);
      ctx.lineTo(best.x, best.y);
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
}

function draw() {
  if (!helix) return;
  ctx.clearRect(0, 0, width, height);
  const { segments, rungs } = helixGeometry();
  drawSegments(segments.filter((s) => s.z < 0));
  rungs.sort((a, b) => a.z - b.z).forEach(drawRung);
  drawSegments(segments.filter((s) => s.z >= 0));
  drawBindings(rungs);
  molecules.forEach(drawMolecule);
}

function step(dt) {
  phase += TWIST_SPEED * dt;
  clock += dt;
  const scale = dt * 60;
  const margin = 40;
  for (const m of molecules) {
    m.x += m.vx * scale;
    m.y += m.vy * scale;
    m.angle += m.spin * scale;
    m.glow += ((m.target || 0) - m.glow) * Math.min(1, dt * 3);
    if (m.x < -margin) m.x = width + margin;
    else if (m.x > width + margin) m.x = -margin;
    if (m.y < -margin) m.y = height + margin;
    else if (m.y > height + margin) m.y = -margin;
  }
}

function loop(time) {
  if (!running) return;
  const dt = lastTime ? Math.min((time - lastTime) / 1000, 0.05) : 0; // clamp after a stall
  lastTime = time;
  step(dt);
  draw();
  frame = requestAnimationFrame(loop);
}

/** Run only when it can be seen and the user hasn't asked for reduced motion. */
function update() {
  const shouldRun = visible && !document.hidden && !reducedMotion.matches;
  if (shouldRun && !running) {
    running = true;
    lastTime = 0;
    frame = requestAnimationFrame(loop);
  } else if (!shouldRun && running) {
    running = false;
    cancelAnimationFrame(frame);
    draw();
  }
}

if (canvas && ctx) {
  new ResizeObserver(resize).observe(hero);
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    update();
  }).observe(hero);
  document.addEventListener("visibilitychange", update);
  reducedMotion.addEventListener("change", update);
  // Colours come from CSS variables, which change with the colour scheme.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", resize);

  hero.addEventListener("pointermove", (event) => {
    const rect = hero.getBoundingClientRect();
    pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    if (!running) draw();
  });
  hero.addEventListener("pointerleave", () => {
    pointer = null;
    if (!running) draw();
  });

  resize();
  update();
}
