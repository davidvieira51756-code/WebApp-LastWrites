"use client";

import React, { useState } from "react";

import { useCatTheme } from "../theme";
import { Text } from "./Text";

type SharedProps = {
  label?: string;
  error?: string;
  wrapperStyle?: React.CSSProperties;
  inputStyle?: React.CSSProperties;
};

type SingleInputProps = SharedProps & Omit<React.InputHTMLAttributes<HTMLInputElement>, "style"> & { multiline?: false };
type MultiInputProps = SharedProps & Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, "style"> & { multiline: true };

export function Input(props: SingleInputProps | MultiInputProps) {
  const t = useCatTheme();
  const [isFocused, setIsFocused] = useState(false);

  const { label, error, wrapperStyle, inputStyle } = props;
  const multiline = "multiline" in props && props.multiline;

  const borderColor = error
    ? t.colors.status.error
    : isFocused
      ? t.colors.border.active
      : t.colors.components.input.border;

  const sharedInputStyle: React.CSSProperties = {
    width: "100%",
    border: `1px solid ${borderColor}`,
    backgroundColor: t.colors.components.input.bg,
    color: t.colors.text.primary,
    borderRadius: multiline ? t.radius.l : t.radius.full,
    padding: `${t.space.s}px ${t.space.m}px`,
    fontFamily: "var(--font-geist-sans), sans-serif",
    fontSize: t.typography.body.fontSize,
    lineHeight: t.typography.body.lineHeight,
    outline: "none",
    transition: "border-color 140ms ease",
    minHeight: multiline ? 108 : undefined,
    resize: multiline ? "vertical" : undefined,
    boxSizing: "border-box",
    ...inputStyle,
  };

  const baseEventHandlers = {
    onFocus: () => setIsFocused(true),
    onBlur: () => setIsFocused(false),
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: t.space.xs, ...wrapperStyle }}>
      {label ? <Text variant="label" color="secondary">{label}</Text> : null}

      {multiline ? (
        <textarea
          {...(props as MultiInputProps)}
          {...baseEventHandlers}
          style={sharedInputStyle}
        />
      ) : (
        <input
          {...(props as SingleInputProps)}
          {...baseEventHandlers}
          style={sharedInputStyle}
        />
      )}

      {error ? <Text variant="caption" color={t.colors.status.error}>{error}</Text> : null}
    </div>
  );
}
