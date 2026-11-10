/**
 * The product surface: the verifier, and nothing else.
 *
 * The seeded dataset pages still exist in `src/pages`, but they are not reachable
 * from here. A router is a statement of what the product is; leaving the old
 * dashboard mounted would make this a demo suite with a verifier attached.
 */
import { Component } from 'react';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
import { ErrorNote } from './components/ui.jsx';
import VerifyShell from './verify/VerifyShell.jsx';
import NewCheck from './verify/NewCheck.jsx';
import RunList from './verify/RunList.jsx';
import RunView from './verify/RunView.jsx';
import Watchlist from './verify/Watchlist.jsx';

class Boundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('Unhandled UI error', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center p-6">
          <div className="w-full max-w-md space-y-3">
            <h1 className="h-display text-[22px] text-ink">A view failed to render</h1>
            <p className="text-[13px] leading-relaxed text-muted">
              The run itself is unaffected — it keeps executing on the server. Reloading usually
              clears this.
            </p>
            <ErrorNote error={this.state.error} />
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-primary"
                onClick={() => this.setState({ error: null })}
              >
                Try again
              </button>
              <button type="button" className="btn-outline" onClick={() => window.location.reload()}>
                Reload
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function NotFound() {
  return (
    <div className="grid place-items-center py-24 text-center animate-fade-up">
      <p className="font-display text-[64px] leading-none text-rule-2">404</p>
      <h1 className="mt-3 h-display text-[20px] text-ink">No such view</h1>
      <p className="mt-1.5 max-w-sm text-[12.5px] text-muted">
        That address does not match anything in the verifier.
      </p>
      <Link to="/" className="btn-primary mt-5">
        Start a check
      </Link>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Boundary>
        <Routes>
          <Route element={<VerifyShell />}>
            <Route index element={<NewCheck />} />
            <Route path="runs" element={<RunList />} />
            <Route path="runs/:runId" element={<RunView />} />
            <Route path="watchlist" element={<Watchlist />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </Boundary>
    </BrowserRouter>
  );
}
