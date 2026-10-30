import ReactMarkdown, { type Components } from "react-markdown";

/**
 * Renders model-written markdown (company research) as formatted text.
 * Models answer in markdown whether asked to or not, and shown raw it reads as
 * "### 1." and "**bold**". Styled element by element because the Tailwind
 * typography plugin (`prose`) isn't installed. Raw HTML in the text is never
 * rendered (react-markdown's default), and links are limited to http(s) by its
 * URL transform, so model output can't inject markup or script.
 */
const components: Components = {
  h1: ({ children }) => <h3 className="mt-4 mb-2 text-base font-semibold first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-4 mb-2 text-base font-semibold first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-4 mb-1.5 font-semibold first:mt-0">{children}</h4>,
  h4: ({ children }) => <h5 className="mt-3 mb-1 font-semibold first:mt-0">{children}</h5>,
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 ml-5 list-disc space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 ml-5 list-decimal space-y-1">{children}</ol>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  // A link whose URL the transform stripped (javascript: and the like) stays text.
  a: ({ href, children }) => href ? (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">
      {children}
    </a>
  ) : <>{children}</>,
  hr: () => <hr className="my-4 border-border" />,
  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 pl-3 text-muted-foreground">{children}</blockquote>,
  code: ({ children }) => <code className="rounded bg-muted px-1 py-0.5 text-[0.85em]">{children}</code>,
};

export function Markdown({ children }: { children: string }) {
  return <ReactMarkdown components={components}>{children}</ReactMarkdown>;
}
