import { useEffect, useRef, useState } from 'react';

/** A 2px vermilion reading rule. Information, not decoration: it is the page's
 * scroll position, so it stays under reduced motion. */
export function ScrollProgress() {
  const ref = useRef(null);

  useEffect(() => {
    const bar = ref.current;
    if (!bar) return undefined;
    let raf = 0;
    const paint = () => {
      raf = 0;
      const doc = document.documentElement;
      const max = doc.scrollHeight - doc.clientHeight;
      const ratio = max > 0 ? Math.min(1, Math.max(0, doc.scrollTop / max)) : 0;
      bar.style.transform = `scaleX(${ratio})`;
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(paint);
    };
    paint();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, []);

  return <div ref={ref} className="read-progress" aria-hidden="true" />;
}

/**
 * A slow band of the vocabulary the product actually uses.
 *
 * It is decorative, so it is hidden from assistive tech and its duplicate track
 * is aria-hidden too — a screen reader should not hear the reason codes twice.
 */
export function Ticker({ items }) {
  const track = [...items, ...items];
  return (
    <div className="ticker" aria-hidden="true">
      <div className="ticker-track">
        {track.map((item, index) => (
          <span key={`${item}-${index}`} className="ticker-item">
            <span className="ticker-dot" />
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * A number that counts to its value the first time it is seen.
 *
 * Only a measurement gets this treatment, and it always settles on the exact
 * figure — the animation is never the source of the value.
 */
export function Counter({ value, suffix = '', decimals = 0 }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(0);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || value === 0 || typeof IntersectionObserver === 'undefined') {
      setShown(value);
      return undefined;
    }

    let raf = 0;
    let start = 0;
    const io = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        io.disconnect();
        const run = (now) => {
          if (!start) start = now;
          const p = Math.min(1, (now - start) / 1100);
          setShown(value * (1 - (1 - p) ** 3));
          if (p < 1) raf = requestAnimationFrame(run);
          else setShown(value);
        };
        raf = requestAnimationFrame(run);
      },
      { threshold: 0.5 },
    );
    io.observe(node);
    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [value]);

  return (
    <span ref={ref} className="tabular-nums">
      {shown.toFixed(decimals)}
      {suffix}
    </span>
  );
}

/** True once the page has scrolled past `at`, for the nav's condensed state. */
export function useScrolled(at = 16) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        setScrolled(window.scrollY > at);
      });
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('scroll', onScroll);
    };
  }, [at]);
  return scrolled;
}
