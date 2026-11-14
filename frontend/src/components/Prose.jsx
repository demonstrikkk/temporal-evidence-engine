/**
 * Model prose, rendered — with the parser loaded only when something needs it.
 *
 * `react-markdown` plus its GFM plugin is a parser, a plugin pipeline and an AST
 * walker. That is worth having wherever a model's words are shown, and it is
 * worth having in none of the code paths that draw a table of runs. So the
 * renderer is a lazy chunk: the route bundle stays where it was, and the chunk
 * arrives the first time an answer is on screen.
 *
 * The fallback is the answer itself in a `<pre>`, not a spinner. The reader sees
 * the words immediately and formatting a moment later; nothing hidden, nothing
 * blank, and no layout jump beyond the formatting itself.
 */
import { lazy, Suspense } from 'react';

const Markdown = lazy(() => import('./Markdown.jsx'));

export default function Prose({ children, className = '', marks = null }) {
  const text = String(children ?? '');
  if (!text.trim()) return null;
  return (
    <Suspense fallback={<pre className="md-pending">{text}</pre>}>
      <Markdown className={className} marks={marks}>
        {text}
      </Markdown>
    </Suspense>
  );
}
