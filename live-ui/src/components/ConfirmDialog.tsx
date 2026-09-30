import { useEffect, useRef } from "react";
import { TriangleAlert } from "lucide-react";
import { Button } from "./ui";

interface Props {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Native modal dialog: focus trap, Escape to cancel and inert background come from the platform. */
export function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
      onClick={(event) => event.target === ref.current && onCancel()}
      aria-labelledby="confirm-title"
      aria-describedby="confirm-body"
      className="m-auto w-[min(440px,calc(100vw-32px))] rounded-2xl border border-line bg-surface p-0 text-ink shadow-2xl backdrop:bg-black/50 backdrop:backdrop-blur-sm"
    >
      <div className="p-6">
        <span className="grid size-11 place-items-center rounded-xl bg-attention-soft text-attention">
          <TriangleAlert className="size-5" aria-hidden />
        </span>
        <h2 id="confirm-title" className="mt-4 text-lg font-semibold tracking-tight">
          {title}
        </h2>
        <p id="confirm-body" className="mt-1.5 text-sm leading-relaxed text-muted">
          {body}
        </p>
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button onClick={onCancel} autoFocus>
            Keep session
          </Button>
          <Button variant="quiet-danger" onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
