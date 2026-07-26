import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

type MarkdownMessageProps = {
  text: string;
};

const markdownComponents: Components = {
  a({ children, href, ...props }) {
    return (
      <a href={href} target="_blank" rel="noreferrer noopener" {...props}>
        {children}
      </a>
    );
  },
  code({ children, className, ...props }) {
    const language = languageFromClassName(className);
    if (language) {
      return (
        <code className="markdown-code" {...props}>
          {children}
        </code>
      );
    }

    return (
      <code className="markdown-inline-code" {...props}>
        {children}
      </code>
    );
  },
  pre({ children }) {
    const language = codeLanguageFromChildren(children);
    return (
      <figure className="markdown-code-block">
        {language ? <figcaption className="markdown-code-language">{language}</figcaption> : null}
        <pre>{children}</pre>
      </figure>
    );
  },
  table({ children }) {
    return (
      <div className="markdown-table-wrap">
        <table>{children}</table>
      </div>
    );
  }
};

export function MarkdownMessage({ text }: MarkdownMessageProps) {
  return (
    <div className="markdown-message">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={markdownComponents}
        skipHtml
      >
        {stripUnsafeHtmlBlocks(text)}
      </ReactMarkdown>
    </div>
  );
}

function stripUnsafeHtmlBlocks(text: string): string {
  return text
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, "");
}

function languageFromClassName(className?: string): string {
  const match = /(?:^|\s)language-([^\s]+)/.exec(className ?? "");
  return match?.[1] ?? "";
}

function codeLanguageFromChildren(children: unknown): string {
  if (!isElementLike(children)) {
    return "";
  }

  const className = children.props?.className;
  return typeof className === "string" ? languageFromClassName(className) : "";
}

function isElementLike(value: unknown): value is { props?: { className?: unknown } } {
  return typeof value === "object" && value !== null && "props" in value;
}
