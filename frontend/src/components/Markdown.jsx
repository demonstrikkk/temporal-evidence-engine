/**
 * Model prose, set as prose.
 *
 * A model answers in Markdown whether or not it is asked to: tables, numbered
 * steps, fenced code and bold spans are how a modern assistant writes. Dumping
 * that into a `<p>` shows the asterisks and loses the table, which reads as a
 * broken renderer rather than as an answer.
 *
 * Two rules keep this from becoming a hole:
 *
 * 1. **No raw HTML.** `react-markdown` ignores embedded HTML unless a rehype
 *    plugin is added, and none is. A model that emits `<script>` gets it as
 *    text, not as markup.
 * 2. **Links open out, never in place.** Every anchor leaves for a new tab with
 *    `rel="noreferrer noopener"`. The default `urlTransform` already refuses
 *    `javascript:` and friends, so the href that arrives here has been checked
 *    once by the library and once by the browser.
 *
 * Headings are demoted, not restyled: an answer that opens with `#` would
 * otherwise put an `h1` inside a page that already has one, and the outline a
 * screen reader announces would claim the answer is the document's title.
 *
 * When `marks` is passed, the claim spans are drawn on the rendered text:
 * `claimMarks.js` wraps the stored offsets in a `<mark>`, and the `mark`
 * override below turns that into the same button the marked view uses. The
 * offsets are the extractor's; nothing here searches for a string.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { markClaims } from './claimMarks.js';

const COMPONENTS = {
  h1: ({ children }) => <h3>{children}</h3>,
  h2: ({ children }) => <h3>{children}</h3>,
  h3: ({ children }) => <h4>{children}</h4>,
  h4: ({ children }) => <h4>{children}</h4>,
  h5: ({ children }) => <p className="md-h">{children}</p>,
  h6: ({ children }) => <p className="md-h">{children}</p>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  // Wide content scrolls inside its own box; the page must never widen because
  // a model wrote a long line in a fenced block.
  table: ({ children }) => (
    <div className="md-scroll">
      <table>{children}</table>
    </div>
  ),
  pre: ({ children }) => <pre className="md-pre">{children}</pre>,
};

function markedComponents(selectedId, onSelect) {
  return {
    ...COMPONENTS,
    mark: ({ node, children, className = '', title }) => {
      const claimId = String(node?.properties?.dataClaim ?? '');
      if (!claimId) return <mark className={className}>{children}</mark>;
      const select = () => onSelect?.(claimId);
      // A `<span role="button">`, not a `<button>`: a button is an inline-block
      // formatting context, and a claim run that ends in a space ("…based in ")
      // loses that space against the next run. A span is inline by nature, so
      // the marked sentence keeps its word spacing and can still be selected
      // and copied as text.
      return (
        <span
          role="button"
          tabIndex={0}
          className={className}
          title={title}
          data-claim={claimId}
          aria-pressed={claimId === selectedId}
          onClick={select}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              select();
            }
          }}
        >
          {children}
        </span>
      );
    },
  };
}

/** A remark plugin is a factory; the closure carries the answer and the spans. */
function claimPlugin(answer, claims) {
  return () => (tree) => {
    markClaims(tree, { answer, claims });
  };
}

export default function Markdown({ children, className = '', marks = null }) {
  const text = String(children ?? '');
  const components = marks ? markedComponents(marks.selectedId, marks.onSelect) : COMPONENTS;
  const plugins = marks ? [remarkGfm, claimPlugin(text, marks.claims)] : [remarkGfm];

  return (
    <div className={`md ${className}`.trim()}>
      <ReactMarkdown remarkPlugins={plugins} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
