import { useEffect, useRef } from 'react';

/**
 * Strata: the home page's procedural ground.
 *
 * The product is about time, so the backdrop is sedimentary — bands of paper
 * laid down in order, one ember seam and one vermilion seam running through
 * them. It is drawn, not photographed, so it costs one small canvas instead of
 * a hero image and it never blocks the text: the layers sit at 6–16% opacity
 * behind a bone scrim.
 *
 * Constraints, in the same spirit as the WebGL field it replaces here:
 *  - 2D context, no library, one canvas, no per-frame allocation of paths
 *  - renders at 1x and scales by CSS; a soft field does not need retina
 *  - pauses when the tab is hidden
 *  - `prefers-reduced-motion` draws one static frame and stops listening
 *  - pointer influence is a phase offset, not a moving highlight, so it reads
 *    as parallax rather than a cursor glow
 */

const LAYERS = 9;

const layerTint = [
  'rgba(195, 178, 152, 0.40)',
  'rgba(216, 202, 180, 0.52)',
  'rgba(228, 214, 191, 0.62)',
  'rgba(176, 42, 28, 0.14)',
  'rgba(228, 214, 191, 0.58)',
  'rgba(216, 202, 180, 0.48)',
  'rgba(214, 100, 34, 0.16)',
  'rgba(228, 214, 191, 0.54)',
  'rgba(195, 178, 152, 0.34)',
];

export default function StrataField() {
  const ref = useRef(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    if (!ctx) return undefined;

    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let width = 0;
    let height = 0;
    let raf = 0;
    let alive = true;
    const pointer = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.max(1, Math.round(rect.width));
      height = Math.max(1, Math.round(rect.height));
      canvas.width = width;
      canvas.height = height;
    };

    const draw = (t) => {
      pointer.x += (pointer.tx - pointer.x) * 0.06;
      pointer.y += (pointer.ty - pointer.y) * 0.06;
      ctx.clearRect(0, 0, width, height);

      const band = height / LAYERS;
      const drift = (pointer.x - 0.5) * 60;
      const rise = (pointer.y - 0.5) * 30;

      for (let i = 0; i < LAYERS; i += 1) {
        const top = i * band - band * 0.35;
        ctx.beginPath();
        ctx.moveTo(-40, height + 40);
        for (let x = -40; x <= width + 40; x += 14) {
          const lead = Math.sin(x * 0.0042 + i * 0.9 + t * 0.00009 + drift * 0.01) * (band * 0.16);
          const echo = Math.sin(x * 0.011 - i * 0.4 - t * 0.00014) * (band * 0.07);
          ctx.lineTo(x, top + lead + echo + rise * (0.04 + i * 0.012));
        }
        ctx.lineTo(width + 40, height + 40);
        ctx.closePath();
        ctx.fillStyle = layerTint[i % layerTint.length];
        ctx.fill();

        // The seam: a single hairline where the layer meets the one above it.
        ctx.beginPath();
        for (let x = -40; x <= width + 40; x += 14) {
          const lead = Math.sin(x * 0.0042 + i * 0.9 + t * 0.00009 + drift * 0.01) * (band * 0.16);
          const echo = Math.sin(x * 0.011 - i * 0.4 - t * 0.00014) * (band * 0.07);
          const y = top + lead + echo + rise * (0.04 + i * 0.012);
          if (x === -40) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.strokeStyle =
          i === 3 ? 'rgba(176, 42, 28, 0.46)' : i === 6 ? 'rgba(214, 100, 34, 0.5)' : 'rgba(74, 58, 48, 0.14)';
        ctx.lineWidth = i === 3 || i === 6 ? 1.2 : 0.75;
        ctx.stroke();
      }
    };

    const loop = (now) => {
      if (!alive) return;
      draw(now);
      raf = requestAnimationFrame(loop);
    };

    const onPointer = (event) => {
      const rect = canvas.getBoundingClientRect();
      pointer.tx = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      pointer.ty = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    };

    const onVisibility = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden && alive && !reduced) raf = requestAnimationFrame(loop);
    };

    resize();
    if (reduced) {
      draw(0);
    } else {
      raf = requestAnimationFrame(loop);
      window.addEventListener('pointermove', onPointer, { passive: true });
      document.addEventListener('visibilitychange', onVisibility);
    }

    const observer = new ResizeObserver(() => {
      resize();
      if (reduced) draw(0);
    });
    observer.observe(canvas);

    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener('pointermove', onPointer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  return <canvas ref={ref} className="strata-canvas" aria-hidden="true" />;
}
