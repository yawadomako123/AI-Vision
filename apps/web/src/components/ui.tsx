import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:brightness-110",
  secondary: "border border-line bg-surface hover:bg-surface-2",
  danger: "border border-bad/40 text-bad hover:bg-bad/10",
  ghost: "text-muted hover:bg-surface-2 hover:text-text",
};

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}

export function Card({ title, action, children, className = "" }: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-surface ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-4 py-10 text-center text-sm text-muted">{children}</p>;
}

export function ErrorNote({ error }: { error: unknown }) {
  return (
    <p role="alert" className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
      {error instanceof Error ? error.message : "Something went wrong"}
    </p>
  );
}

export const inputCls =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm placeholder:text-muted focus:border-accent";

export const selectCls = "rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm";
