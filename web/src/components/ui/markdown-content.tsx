import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

const markdownComponents: Components = {
  h1: ({ children }) => (
    <h1 className="mb-3 mt-7 text-xl font-semibold leading-8 text-[var(--tc-text-primary)]">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-3 mt-7 text-lg font-semibold leading-7 text-[var(--tc-text-primary)]">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-2 mt-5 text-[15px] font-medium leading-7 text-[var(--tc-text-secondary)]">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="mb-2 mt-4 text-sm font-medium leading-6 text-[var(--tc-text-secondary)]">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="my-3 whitespace-pre-wrap text-[var(--tc-text-primary)]">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="my-3 list-disc space-y-2 pl-5 text-[var(--tc-text-secondary)] marker:text-[var(--tc-text-muted)]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-3 list-decimal space-y-2 pl-5 text-[var(--tc-text-secondary)] marker:font-mono marker:text-xs marker:text-[var(--tc-text-muted)]">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1 leading-7">{children}</li>,
  strong: ({ children }) => (
    <strong className="font-semibold text-[var(--tc-text-primary)]">
      {children}
    </strong>
  ),
  em: ({ children }) => (
    <em className="text-[var(--tc-text-secondary)]">{children}</em>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-4 pl-3 text-[var(--tc-text-secondary)] italic">
      {children}
    </blockquote>
  ),
  a: ({ children, href }) => (
    <a
      className="font-medium text-[var(--tc-text-primary)] underline decoration-[var(--tc-text-muted)] underline-offset-4 hover:decoration-[var(--tc-text-primary)]"
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {children}
    </a>
  ),
  code: ({ children, className }) => {
    const isBlock = Boolean(className) || String(children).includes("\n");
    return (
      <code
        className={cn(
          "font-mono text-[13px] text-[var(--tc-text-secondary)]",
          !isBlock &&
            "rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] px-1.5 py-0.5",
          className,
        )}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-4 max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] px-3 py-2.5 font-mono text-[13px] leading-6 text-[var(--tc-text-secondary)] [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <table className="my-4 block max-w-full overflow-x-auto text-left text-sm text-[var(--tc-text-secondary)]">
      {children}
    </table>
  ),
  thead: ({ children }) => (
    <thead className="text-[var(--tc-text-primary)]">{children}</thead>
  ),
  th: ({ children }) => <th className="px-3 py-2 font-medium">{children}</th>,
  td: ({ children }) => <td className="px-3 py-2 align-top">{children}</td>,
  hr: () => <span aria-hidden="true" className="block h-3" />,
};

type MarkdownContentProps = ComponentPropsWithoutRef<"div"> & {
  content: string;
};

export function MarkdownContent({
  className,
  content,
  ...props
}: MarkdownContentProps) {
  return (
    <div
      className={cn(
        "min-w-0 break-words text-[15px] leading-7 text-[var(--tc-text-primary)] [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className,
      )}
      {...props}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
