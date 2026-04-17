"use client";

import React from "react";

import { useCatTheme } from "../theme";

type TextVariant = "h1" | "h2" | "h3" | "body" | "bodySmall" | "label" | "caption";
type TextColor = "primary" | "secondary" | "muted" | "inverse" | "brand";
type TextWeight = "normal" | "medium" | "semibold" | "bold";

type TextProps = {
  children: React.ReactNode;
  variant?: TextVariant;
  color?: TextColor | string;
  weight?: TextWeight;
  style?: React.CSSProperties;
  numberOfLines?: number;
};

const weightMap: Record<TextWeight, React.CSSProperties["fontWeight"]> = {
  normal: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
};

export function Text({
  children,
  variant = "body",
  color = "primary",
  weight,
  style,
  numberOfLines,
}: TextProps) {
  const t = useCatTheme();

  const variantMap: Record<TextVariant, React.CSSProperties> = {
    h1: t.typography.h1,
    h2: t.typography.h2,
    h3: t.typography.h3,
    body: t.typography.body,
    bodySmall: t.typography.bodySmall,
    label: t.typography.label,
    caption: t.typography.caption,
  };

  const colorMap: Record<TextColor, string> = {
    primary: t.colors.text.primary,
    secondary: t.colors.text.secondary,
    muted: t.colors.text.muted,
    inverse: t.colors.text.inverse,
    brand: t.colors.brand,
  };

  const lineClamp: React.CSSProperties =
    typeof numberOfLines === "number"
      ? {
          display: "-webkit-box",
          WebkitLineClamp: numberOfLines,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }
      : {};

  const Component = variant === "h1" ? "h1" : variant === "h2" ? "h2" : variant === "h3" ? "h3" : "p";

  return (
    <Component
      style={{
        margin: 0,
        fontFamily: "var(--font-geist-sans), sans-serif",
        ...variantMap[variant],
        color: color in colorMap ? colorMap[color as TextColor] : color,
        fontWeight: weight ? weightMap[weight] : variantMap[variant].fontWeight,
        ...lineClamp,
        ...style,
      }}
    >
      {children}
    </Component>
  );
}
