import { useEffect, useRef, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}

const supportsClosedBy = typeof HTMLDialogElement !== "undefined" && "closedBy" in HTMLDialogElement.prototype;

/** Native modal <dialog>: focus trap, Esc and inert background come from showModal(). */
export function Modal({ open, onClose, title, children, wide }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = `dlg-${title.replace(/\W+/g, "-").toLowerCase()}`;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Light-dismiss fallback for browsers without <dialog closedby> (Safari).
  const onClick = (e: React.MouseEvent<HTMLDialogElement>) => {
    if (supportsClosedBy || e.target !== ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const inside = r.top <= e.clientY && e.clientY <= r.bottom && r.left <= e.clientX && e.clientX <= r.right;
    if (!inside) ref.current.close();
  };

  return (
    <dialog
      ref={ref}
      closedby="any"
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={onClick}
      className={`m-auto w-[calc(100%-2rem)] ${wide ? "max-w-4xl" : "max-w-lg"} max-h-[calc(100dvh-2rem)] rounded-xl border border-line p-0 shadow-2xl`}
    >
      <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-3">
        <h2 id={titleId} className="text-base font-semibold">{title}</h2>
        <button
          type="button"
          onClick={() => ref.current?.close()}
          className="rounded-md px-2 py-1 text-muted hover:bg-surface-2 hover:text-text"
          aria-label="Close"
        >
          ✕
        </button>
      </div>
      <div className="p-5">{open && children}</div>
    </dialog>
  );
}
