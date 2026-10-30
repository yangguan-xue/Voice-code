import { Download, ImageOff, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { WebDemoClientLike } from "../../lib/bridge/protocol";

type GeneratedImageCardProps = {
  path: string;
  client: WebDemoClientLike;
  sessionToken: string;
};

export function GeneratedImageCard({ path, client, sessionToken }: GeneratedImageCardProps) {
  const [objectUrl, setObjectUrl] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let createdUrl = "";
    setObjectUrl("");
    setFailed(false);
    client
      .getSandboxFile(sessionToken, path)
      .then((file) => {
        if (cancelled) {
          return;
        }
        if (file.encoding === "base64" && file.content) {
          // Blob URLs download reliably on mobile browsers; multi-MB data:
          // URLs are silently dropped by many mobile webviews.
          const bytes = Uint8Array.from(atob(file.content), (char) => char.charCodeAt(0));
          const blob = new Blob([bytes], { type: file.mimeType || "image/png" });
          createdUrl = URL.createObjectURL(blob);
          setObjectUrl(createdUrl);
        } else {
          setFailed(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
      if (createdUrl) {
        URL.revokeObjectURL(createdUrl);
      }
    };
  }, [client, path, sessionToken]);

  const fileName = path.split("/").filter(Boolean).at(-1) || "image.png";

  if (failed) {
    return (
      <p className="generated-image-status generated-image-error" role="status">
        <ImageOff size={15} strokeWidth={1.8} aria-hidden="true" />
        图片加载失败：{path}
      </p>
    );
  }
  if (!objectUrl) {
    return (
      <p className="generated-image-status" role="status">
        <Loader2 size={15} strokeWidth={1.8} className="tool-state-running" aria-hidden="true" />
        正在加载生成的图片…
      </p>
    );
  }
  return (
    <figure className="generated-image-card">
      <img src={objectUrl} alt={fileName} />
      <figcaption>
        <span className="generated-image-name">{fileName}</span>
        <a className="generated-image-download" href={objectUrl} download={fileName} target="_blank" rel="noreferrer">
          <Download size={15} strokeWidth={1.8} aria-hidden="true" />
          下载
        </a>
      </figcaption>
    </figure>
  );
}
