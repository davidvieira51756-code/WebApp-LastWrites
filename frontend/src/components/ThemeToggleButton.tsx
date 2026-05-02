"use client";

import { Button, useCatTheme } from "@/components/catmagui";

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 14.2A8 8 0 0 1 9.8 4a8.5 8.5 0 1 0 10.2 10.2Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M12 2v2.5M12 19.5V22M22 12h-2.5M4.5 12H2M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8M19.1 19.1l-1.8-1.8M6.7 6.7 4.9 4.9"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function ThemeToggleButton() {
  const t = useCatTheme();
  const label = t.isDark ? "Switch to light mode" : "Switch to dark mode";

  return (
    <Button
      type="button"
      variant="Primary"
      onClick={t.toggle}
      aria-label={label}
      title={label}
      style={{
        width: 40,
        height: 40,
        minWidth: 40,
        padding: 0,
        borderRadius: 999,
      }}
    >
      {t.isDark ? <SunIcon /> : <MoonIcon />}
    </Button>
  );
}
