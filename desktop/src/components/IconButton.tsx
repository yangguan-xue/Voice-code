import type { ReactNode } from "react";

type IconButtonProps = {
  label: string;
  children: ReactNode;
  tone?: "default" | "accent" | "send";
  type?: "button" | "submit";
  disabled?: boolean;
  onClick?: () => void;
};

export function IconButton({
  label,
  children,
  tone = "default",
  type = "button",
  disabled = false,
  onClick
}: IconButtonProps) {
  return (
    <button
      className={`icon-button icon-button-${tone}`}
      type={type}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
