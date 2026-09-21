"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Skill pile -- the only physics in the app.
 *
 * Each skill tagged on a compliant bullet drops in as a pill and settles into a
 * heap, sized by how many bullets carry it. It answers one question the rest of
 * the dashboard does not: what has actually accumulated. A bar chart would say
 * the same thing more precisely, and if this ever competes with the numbers for
 * attention it should be deleted rather than tuned.
 *
 * Engineering notes, in rough order of how much trouble they cause
 * ---------------------------------------------------------------
 * 1. `matter-js` is imported inside the effect, not at module scope. Effects do
 *    not run during SSR, so the engine can never touch `window` on the server,
 *    and the library stays out of the initial JS payload.
 * 2. Teardown stops the runner, clears the world, and cancels the frame loop.
 *    Matter keeps its own references, so a component that only cancels its RAF
 *    leaks an entire physics world per navigation -- and App Router remounts
 *    on every route change.
 * 3. The simulation pauses when scrolled out of view. A physics loop running
 *    behind a page the user is not looking at is pure battery cost.
 * 4. `prefers-reduced-motion` is respected by not simulating at all: the pills
 *    are drawn in a settled grid instead. The information is the set of skills
 *    and their sizes, and none of that requires movement to read.
 */

export type SkillWeight = { name: string; count: number };

type Props = {
  skills: SkillWeight[];
  /** Canvas height in CSS pixels. Width follows the container. */
  height?: number;
};

const PILL_HEIGHT = 26;
const CHAR_WIDTH = 7.2;
const PILL_PADDING = 18;

function pillWidth(name: string): number {
  return Math.min(200, Math.max(52, name.length * CHAR_WIDTH + PILL_PADDING));
}

export function SkillPile({ skills, height = 260 }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(query.matches);
    const onChange = (event: MediaQueryListEvent) => setReducedMotion(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas || skills.length === 0) return;

    const context = canvas.getContext("2d");
    if (!context) return;

    // Everything the teardown needs to reach, captured so the cleanup closure
    // does not depend on refs that may already be null by the time it runs.
    let frame = 0;
    let disposed = false;
    let visible = true;
    let engine: import("matter-js").Engine | null = null;
    let runner: import("matter-js").Runner | null = null;
    let bodies: import("matter-js").Body[] = [];

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let width = container.clientWidth;

    function sizeCanvas() {
      if (!canvas) return;
      width = container?.clientWidth ?? width;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context?.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function drawPill(
      x: number,
      y: number,
      angle: number,
      w: number,
      label: string,
      count: number,
    ) {
      if (!context) return;
      context.save();
      context.translate(x, y);
      context.rotate(angle);

      const r = PILL_HEIGHT / 2;
      context.beginPath();
      context.roundRect(-w / 2, -r, w, PILL_HEIGHT, r);
      context.fillStyle = "#ffffff";
      context.fill();
      context.lineWidth = 1;
      context.strokeStyle = "#c3cad4";
      context.stroke();

      context.fillStyle = "#14161a";
      context.font = '500 11px var(--font-instrument), system-ui, sans-serif';
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(label, -4, 0.5);

      // The count sits in mono, like every other number in the product.
      context.fillStyle = "#8a2e5d";
      context.font = '600 10px var(--font-mono-jb), ui-monospace, monospace';
      context.fillText(`${count}`, w / 2 - 11, 0.5);

      context.restore();
    }

    async function start() {
      const Matter = await import("matter-js");
      if (disposed) return;

      sizeCanvas();

      engine = Matter.Engine.create({ gravity: { x: 0, y: 0.9, scale: 0.001 } });

      const walls = [
        // Floor and side walls, thick enough that a fast body cannot tunnel
        // through them at low frame rates.
        Matter.Bodies.rectangle(width / 2, height + 30, width + 200, 60, { isStatic: true }),
        Matter.Bodies.rectangle(-30, height / 2, 60, height * 3, { isStatic: true }),
        Matter.Bodies.rectangle(width + 30, height / 2, 60, height * 3, { isStatic: true }),
      ];

      bodies = skills.map((skill, index) => {
        const w = pillWidth(skill.name);
        return Matter.Bodies.rectangle(
          // Spread the drop points so the pile does not form a single column.
          40 + ((index * 97) % Math.max(1, width - 80)),
          -60 - index * 34,
          w,
          PILL_HEIGHT,
          {
            chamfer: { radius: PILL_HEIGHT / 2 },
            restitution: 0.18,
            friction: 0.55,
            frictionAir: 0.02,
            // Heavier for more-used skills, so the common ones settle at the
            // bottom of the pile rather than skittering across the top.
            density: 0.001 * (1 + skill.count * 0.25),
          },
        );
      });

      Matter.Composite.add(engine.world, [...walls, ...bodies]);
      runner = Matter.Runner.create();
      Matter.Runner.run(runner, engine);

      const loop = () => {
        if (disposed) return;
        frame = requestAnimationFrame(loop);
        if (!visible || !context) return;

        context.clearRect(0, 0, width, height);
        bodies.forEach((body, index) => {
          const skill = skills[index];
          if (!skill) return;
          drawPill(
            body.position.x,
            body.position.y,
            body.angle,
            pillWidth(skill.name),
            skill.name,
            skill.count,
          );
        });
      };
      frame = requestAnimationFrame(loop);
    }

    // Static fallback: a settled grid, no engine, no loop.
    function drawStatic() {
      sizeCanvas();
      if (!context) return;
      context.clearRect(0, 0, width, height);
      let x = 12;
      let y = height - PILL_HEIGHT;
      for (const skill of skills) {
        const w = pillWidth(skill.name);
        if (x + w > width - 12) {
          x = 12;
          y -= PILL_HEIGHT + 6;
        }
        if (y < 0) break;
        drawPill(x + w / 2, y + PILL_HEIGHT / 2, 0, w, skill.name, skill.count);
        x += w + 6;
      }
    }

    const observer = new IntersectionObserver(
      (entries) => {
        visible = entries[0]?.isIntersecting ?? true;
      },
      { threshold: 0.05 },
    );
    observer.observe(container);

    const onResize = () => {
      if (reducedMotion) drawStatic();
      else sizeCanvas();
    };
    window.addEventListener("resize", onResize);

    if (reducedMotion) {
      drawStatic();
    } else {
      void start();
    }

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", onResize);

      // Order matters: stop the runner before clearing the world, or the runner
      // ticks a world that is being mutated underneath it.
      if (runner) {
        void import("matter-js").then((Matter) => {
          if (runner) Matter.Runner.stop(runner);
          if (engine) {
            Matter.Composite.clear(engine.world, false);
            Matter.Engine.clear(engine);
          }
          engine = null;
          runner = null;
          bodies = [];
        });
      }
    };
  }, [skills, height, reducedMotion]);

  if (skills.length === 0) {
    return (
      <p className="px-5 py-8 text-center text-sm text-muted">
        Skills appear here once bullets are tagged.
      </p>
    );
  }

  return (
    <div ref={containerRef} className="w-full overflow-hidden">
      {/* The canvas is decorative -- the same skills are listed in text below
          it in the dashboard, so a screen reader loses nothing by skipping it. */}
      <canvas ref={canvasRef} aria-hidden="true" className="block" />
    </div>
  );
}
