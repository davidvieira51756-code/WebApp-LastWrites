"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { darkColors, lightColors, radius, spacing, typography, type ThemeColors } from "./tokens";

const STORAGE_KEY = "catmagui.theme.mode";

export type CatThemeMode = "light" | "dark" | "system";

export type CatTheme = {
  colors: ThemeColors;
  space: typeof spacing;
  radius: typeof radius;
  typography: typeof typography;
  mode: CatThemeMode;
  isDark: boolean;
  setMode: (mode: CatThemeMode) => void;
  toggle: () => void;
};

const CatThemeContext = createContext<CatTheme | null>(null);

type CatmaguiThemeProviderProps = {
  children: React.ReactNode;
  initialMode?: CatThemeMode;
};

export function CatmaguiThemeProvider({
  children,
  initialMode = "system",
}: CatmaguiThemeProviderProps) {
  const [mode, setModeState] = useState<CatThemeMode>(initialMode);
  const [prefersDark, setPrefersDark] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const persistedMode = window.localStorage.getItem(STORAGE_KEY) as CatThemeMode | null;
    if (persistedMode === "light" || persistedMode === "dark" || persistedMode === "system") {
      setModeState(persistedMode);
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    setPrefersDark(mediaQuery.matches);

    const listener = (event: MediaQueryListEvent) => {
      setPrefersDark(event.matches);
    };

    mediaQuery.addEventListener("change", listener);
    return () => mediaQuery.removeEventListener("change", listener);
  }, []);

  const isDark = mode === "system" ? prefersDark : mode === "dark";

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }

    const effectiveMode = isDark ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", effectiveMode);
    document.documentElement.style.colorScheme = effectiveMode;
    document.body.style.backgroundColor = isDark ? darkColors.bg : lightColors.bg;
  }, [isDark]);

  const setMode = useCallback((nextMode: CatThemeMode) => {
    setModeState(nextMode);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, nextMode);
    }
  }, []);

  const toggle = useCallback(() => {
    setMode(mode === "dark" ? "light" : "dark");
  }, [mode, setMode]);

  const value = useMemo<CatTheme>(
    () => ({
      colors: isDark ? darkColors : lightColors,
      space: spacing,
      radius,
      typography,
      mode,
      isDark,
      setMode,
      toggle,
    }),
    [isDark, mode, setMode, toggle]
  );

  return <CatThemeContext.Provider value={value}>{children}</CatThemeContext.Provider>;
}

export function useCatTheme(): CatTheme {
  const context = useContext(CatThemeContext);
  if (!context) {
    throw new Error("useCatTheme must be used inside CatmaguiThemeProvider.");
  }
  return context;
}
