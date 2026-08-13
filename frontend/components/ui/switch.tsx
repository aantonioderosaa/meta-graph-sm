"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export interface SwitchProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}

const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  (
    {
      className,
      checked = false,
      onCheckedChange,
      disabled,
      ...props
    },
    ref,
  ) => {
    return (
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        ref={ref}
        className={cn(
          "inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-input transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          "disabled:pointer-events-none disabled:opacity-50",
          checked ? "border-primary bg-primary" : "bg-input",
          className,
        )}
        onClick={(event) => {
          event.preventDefault();
          if (disabled) return;
          onCheckedChange?.(!checked);
        }}
        {...props}
      >
        <span
          className={cn(
            "pointer-events-none block h-4 w-4 rounded-full bg-background shadow-sm transition-transform",
            checked ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </button>
    );
  },
);
Switch.displayName = "Switch";

export { Switch };
