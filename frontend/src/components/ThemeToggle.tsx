"use client";

import { Button, useCatTheme } from "@/components/catmagui";

export default function ThemeToggle() {
  const t = useCatTheme();

  return (
    <div
      style={{
        position: "fixed",
        top: t.space.m,
        right: t.space.m,
        zIndex: 50,
      }}
    >
      <Button type="button" variant="Primary" onClick={t.toggle}>
        {t.isDark ? "Light Theme" : "Dark Theme"}
      </Button>
    </div>
  );
}
