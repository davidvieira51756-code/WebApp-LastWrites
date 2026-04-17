"use client";

import React from "react";

import { useCatTheme } from "../theme";
import { Text } from "./Text";

type BadgeVariant = "default" | "success" | "warning" | "error";
type BadgeSize = "sm" | "md";

type BadgeProps = {
  label: string;
  variant?: BadgeVariant;
  size?: BadgeSize;
  outlineOnly?: boolean;
  style?: React.CSSProperties;
};

export function Badge({
  label,
  variant = "default",
  size = "md",
  outlineOnly,
  style,
}: BadgeProps) {
  const t = useCatTheme();

  const colorSet = {
    default: {
      bg: t.colors.components.solid.primary,
      text: t.colors.text.inverse,
      border: t.colors.components.solid.primary,
    },
    success: {
      bg: t.colors.status.success,
      text: t.colors.text.inverse,
      border: t.colors.status.success,
    },
    warning: {
      bg: t.colors.status.warning,
      text: t.colors.text.primary,
      border: t.colors.status.warning,
    },
    error: {
      bg: t.colors.status.error,
      text: t.colors.text.inverse,
      border: t.colors.status.error,
    },
  }[variant];

  const verticalPadding = size === "sm" ? t.space.xxs : t.space.xs;
  const horizontalPadding = size === "sm" ? t.space.s : t.space.m;

  const isOutline = outlineOnly ?? t.isDark;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: size === "sm" ? 22 : 30,
        borderRadius: t.radius.full,
        padding: `${verticalPadding}px ${horizontalPadding}px`,
        border: `1px solid ${colorSet.border}`,
        backgroundColor: isOutline ? "transparent" : colorSet.bg,
        ...style,
      }}
    >
      <Text
        variant="caption"
        weight="semibold"
        color={isOutline ? colorSet.border : colorSet.text}
      >
        {label}
      </Text>
    </span>
  );
}
