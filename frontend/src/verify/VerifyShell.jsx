/**
 * The shell: one product, three destinations.
 *
 * The old dataset workspace is still in the tree but is not routed from here.
 * A verifier that also shows buckets and library pages reads as a demo suite;
 * this reads as a tool.
 */
import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { api } from '../lib/api';
import './app.css';

function NavItem({ to, children, end }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `relative rounded-sm px-2.5 py-1.5 text-[12.5px] font-medium transition-colors duration-150 ${
          isActive ? 'text-ink' : 'text-muted hover:text-ink'
        }`
      }
    >
      {({ isActive }) => (
        <>
          {isActive ? (
            <span
              className="absolute inset-x-1.5 -bottom-px h-0.5 rounded-full bg-accent"
              aria-hidden="true"
            />
          ) : null}
          {children}
        </>
      )}
    </NavLink>
  );
}

function useHealth() {
  const [health, setHealth] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.verify
        .health()
        .then((data) => {
          if (alive) {
            setHealth(data);
            setFailed(false);
          }
        })
        .catch(() => alive && setFailed(true));
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return { health, failed };
}

export default function VerifyShell() {
  const { health, failed } = useHealth();
  const gatewayOk = Boolean(health?.gateway?.ok);
  const tavilyOk = Boolean(health?.tavily?.configured);
  const dot = failed || !health ? 'bg-muted' : gatewayOk && tavilyOk ? 'bg-ok' : 'bg-warn';

  const statusTitle = failed
    ? 'The API is unreachable. Runs cannot start.'
    : !health
      ? 'Checking the gateway…'
      : [
          `gateway: ${gatewayOk ? 'reachable' : health.gateway?.detail || 'unreachable'}`,
          `tavily: ${tavilyOk ? 'configured' : 'no key'}`,
          `cache: ${health.cache_mode}`,
          `rule: ${health.rule_version}`,
          `store: ${health.store}`,
        ].join('\n');

  return (
    <div className="flex min-h-full flex-col">
      <div className="app-grain" aria-hidden="true" />
      <header className="shell-head sticky top-0 z-20 border-b border-rule bg-paper/88 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1500px] items-center gap-5 px-5">
          <NavLink to="/" className="flex min-w-0 items-center gap-2.5" aria-label="Afterfact - home">
            <img src="/tee-mark.svg" width="28" height="28" alt="" className="h-7 w-7 shrink-0" />
            <span className="min-w-0">
              <span className="block truncate font-display text-[15px] leading-none text-ink">
                Afterfact
              </span>
              <span className="mt-0.5 block truncate font-mono text-[9.5px] uppercase tracking-[0.18em] text-muted">
                claim-level verification
              </span>
            </span>
          </NavLink>

          <nav className="ml-2 hidden items-center gap-1 sm:flex" aria-label="Primary">
            <NavItem to="/check">New check</NavItem>
            <NavItem to="/playground">Playground</NavItem>
          <NavItem to="/compare">Compare</NavItem>
            <NavItem to="/runs">Runs</NavItem>
            <NavItem to="/watchlist">Watchlist</NavItem>
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <div
              className="hidden items-center gap-2 text-[11px] text-muted md:flex"
              title={statusTitle}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
              <span className="font-mono">
                {failed ? 'api offline' : health ? `rule ${health.rule_version}` : 'connecting'}
              </span>
            </div>
          </div>
        </div>
      </header>

      <main className="shell-layer mx-auto w-full max-w-[1500px] flex-1 px-4 py-6 sm:px-5">
        <Outlet />
      </main>

      <footer className="border-t border-rule px-5 py-4 text-[11px] text-muted">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-2">
          <span>
            A verdict assesses the cited sources. It is not a statement of absolute truth.
          </span>
          <span className="font-mono">
            {health?.store ? `store: ${health.store}` : ''}
            {health?.cache_mode ? ` · cache: ${health.cache_mode}` : ''}
          </span>
        </div>
      </footer>
    </div>
  );
}
