import { useCallback, useEffect, useRef, useState } from 'react';
import { api, openStream } from '../lib/api';

const TERMINAL = new Set(['completed', 'partial', 'failed']);

/**
 * One run, kept current by two mechanisms on purpose.
 *
 * SSE is the fast path: a claim settles and the register updates immediately.
 * Polling is the truth path: if the browser drops the stream, a proxy buffers
 * it, or the worker emits two events between frames, the sequence-numbered
 * `/events` endpoint still fills the gap. Neither is a fallback for the other —
 * events are deduplicated by sequence, so running both cannot double-count.
 */
export function useVerifyRun(runId) {
  const [run, setRun] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [streamState, setStreamState] = useState('connecting');
  const cursor = useRef(0);
  const seen = useRef(new Set());
  const alive = useRef(true);
  const refreshTimer = useRef(null);

  const merge = useCallback((incoming) => {
    if (!incoming?.length) return;
    setEvents((current) => {
      const next = [...current];
      for (const event of incoming) {
        const key = event.sequence ?? `${event.event_type}:${next.length}`;
        if (seen.current.has(key)) continue;
        seen.current.add(key);
        next.push(event);
        cursor.current = Math.max(cursor.current, Number(event.sequence) || 0);
      }
      next.sort((a, b) => Number(a.sequence) - Number(b.sequence));
      return next.slice(-400);
    });
  }, []);

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const [nextRun, nextEvents] = await Promise.all([
        api.verify.run(runId),
        api.verify.events(runId, 0),
      ]);
      if (!alive.current) return;
      setRun(nextRun);
      merge(nextEvents.events || []);
      setError(null);
    } catch (err) {
      if (alive.current) setError(err);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [runId, merge]);

  useEffect(() => {
    alive.current = true;
    cursor.current = 0;
    seen.current = new Set();
    setEvents([]);
    setRun(null);
    setLoading(true);
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      alive.current = false;
      clearInterval(timer);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [refresh]);

  useEffect(() => {
    if (!runId) return undefined;
    let closed = false;
    const controller = new AbortController();

    (async () => {
      try {
        await openStream(`/verify/runs/${encodeURIComponent(runId)}/stream`, {
          signal: controller.signal,
          onOpen: () => alive.current && setStreamState('live'),
          onFrame: ({ event, data }) => {
            if (!alive.current) return;
            if (event === 'stream.end') {
              setStreamState('ended');
              refresh();
              return;
            }
            if (event === 'stream.heartbeat' || event === 'stream.error') return;
            const payload = data && typeof data === 'object' ? data : {};
            merge([
              {
                sequence: payload.sequence ?? cursor.current + 1,
                event_type: event,
                payload: payload.payload ?? payload,
                created_at: payload.created_at,
              },
            ]);
            if (
              event.startsWith('claim.') ||
              event.startsWith('source.') ||
              event.startsWith('run.') ||
              event === 'claims.extracted'
            ) {
              if (refreshTimer.current) clearTimeout(refreshTimer.current);
              refreshTimer.current = setTimeout(refresh, 250);
            }
          },
        });
      } catch (err) {
        if (!alive.current || controller.signal.aborted) return;
        setStreamState('polling');
        // Polling is already running; a stream failure is not a run failure.
      }
    })();

    return () => {
      closed = true;
      controller.abort();
      if (!closed) setStreamState('closed');
    };
  }, [runId, merge, refresh]);

  const terminal = run ? TERMINAL.has(run.status) : false;
  return { run, events, error, loading, terminal, streamState, refresh };
}

/** Selected claim detail, refetched whenever the run's claim version changes. */
export function useClaim(runId, claimId, version) {
  const [claim, setClaim] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId || !claimId) {
      setClaim(null);
      return undefined;
    }
    let alive = true;
    setLoading(true);
    api.verify
      .claim(runId, claimId)
      .then((data) => {
        if (alive) {
          setClaim(data);
          setError(null);
        }
      })
      .catch((err) => alive && setError(err))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [runId, claimId, version]);

  return { claim, error, loading };
}
