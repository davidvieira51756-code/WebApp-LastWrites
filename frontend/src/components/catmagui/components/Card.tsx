"use client";

import React from "react";

import { useCatTheme } from "../theme";

type CardVariant = "default" | "secondary" | "elevated" | "outline" | "invisible";

type CardProps = {
  children: React.ReactNode;
  variant?: CardVariant;
  style?: React.CSSProperties;
};

export function Card({ children, variant = "default", style }: CardProps) {
  const t = useCatTheme();

  const variants: Record<CardVariant, React.CSSProperties> = {
    default: {
      backgroundColor: t.colors.card.primary,
      border: `1px solid ${t.colors.border.default}`,
    },
    secondary: {
      backgroundColor: t.colors.card.secondary,
      border: `1px solid ${t.colors.border.default}`,
    },
    elevated: {
      backgroundColor: t.colors.card.primary,
      border: `1px solid ${t.colors.border.default}`,
      boxShadow: t.isDark
        ? "0 16px 48px rgba(0, 0, 0, 0.45)"
        : "0 16px 48px rgba(9, 9, 11, 0.08)",
    },
    outline: {
      backgroundColor: "transparent",
      border: `1px dashed ${t.colors.border.active}`,
    },
    invisible: {
      backgroundColor: "transparent",
      border: "none",
    },
  };

  return (
    <section
      style={{
        borderRadius: t.radius.xl,
        padding: t.space.l,
        display: "flex",
        flexDirection: "column",
        gap: t.space.s,
        ...variants[variant],
        ...style,
      }}
    >
      {children}
    </section>
  );
}
