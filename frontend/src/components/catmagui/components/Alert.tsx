"use client";

import React from "react";

import { useCatTheme } from "../theme";
import { Text } from "./Text";

type AlertVariant = "info" | "success" | "warning" | "error";

type AlertProps = {
  title?: string;
  message: string;
  variant?: AlertVariant;
  style?: React.CSSProperties;
};

export function Alert({ title, message, variant = "info", style }: AlertProps) {
  const t = useCatTheme();

  const tone = {
    info: t.colors.brand,
    success: t.colors.status.success,
    warning: t.colors.status.warning,
    error: t.colors.status.error,
  }[variant];

  return (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: t.space.xs,
        padding: t.space.m,
        borderRadius: t.radius.m,
        border: `1px solid ${tone}`,
        borderLeftWidth: 4,
        backgroundColor: t.colors.card.secondary,
        ...style,
      }}
    >
      {title ? <Text variant="label">{title}</Text> : null}
      <Text variant="bodySmall" color="secondary">
        {message}
      </Text>
    </div>
  );
}
