import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "../lib/utils";

export function SearchInput({
  value,
  onChange,
  placeholder = "Search...",
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const [local, setLocal] = useState(value);
  const [previousValue, setPreviousValue] = useState(value);
  const onChangeRef = useRef(onChange);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reflect resets from the parent as well as locally typed text.
  if (value !== previousValue) {
    setPreviousValue(value);
    setLocal(value);
  }

  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  useEffect(() => {
    if (local === value) return;
    const timer = setTimeout(() => onChangeRef.current(local), 300);
    return () => clearTimeout(timer);
  }, [local, value]);

  return (
    <div className={cn("relative w-full min-w-0 sm:w-72", className)}>
      <Search aria-hidden="true" className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        ref={inputRef}
        type="search"
        aria-label={placeholder}
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        placeholder={placeholder}
        className="h-10 bg-card pl-9 pr-9 [&::-webkit-search-cancel-button]:appearance-none"
      />
      {local && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => { setLocal(""); onChange(""); inputRef.current?.focus(); }}
          className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
