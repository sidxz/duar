import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  isPending?: boolean;
  confirmInput?: string;
  confirmInputValue?: string;
  onConfirmInputChange?: (v: string) => void;
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  isPending = false,
  confirmInput,
  confirmInputValue,
  onConfirmInputChange,
}: Props) {
  const disabled = isPending || (confirmInput ? confirmInputValue !== confirmInput : false);

  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{message}</AlertDialogDescription>
        </AlertDialogHeader>

        {confirmInput && (
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">
              Type <span className="font-mono text-foreground">{confirmInput}</span> to confirm
            </label>
            <Input
              value={confirmInputValue ?? ""}
              onChange={(e) => onConfirmInputChange?.(e.target.value)}
              autoFocus
            />
          </div>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <Button
            onClick={onConfirm}
            disabled={disabled}
            variant={danger ? "destructive" : "default"}
          >
            {isPending ? "…" : confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
