"use client";

import Link from "next/link";
import React, { useState } from "react";

import { useCatTheme } from "../theme";
import { Text } from "./Text";

type ButtonVariant =
  | "Default"
  | "SolidPrimary"
  | "Primary"
  | "Secondary"
  | "SolidSecondary"
  | "Destructive"
  | "Invisible";

type ButtonSize = "default" | "primary" | "full" | "link";

type CommonProps = {
  children?: React.ReactNode;
  label?: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  style?: React.CSSProperties;
  leftSlot?: React.ReactNode;
  rightSlot?: React.ReactNode;
};

type ButtonProps = CommonProps & React.ButtonHTMLAttributes<HTMLButtonElement>;

type ButtonLinkProps = CommonProps & {
  href: string;
} & Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href">;

function hexToRgba(hex: string, opacity: number): string {
  const stripped = hex.replace("#", "").trim();
  if (!/^[0-9A-Fa-f]{6}$/.test(stripped)) {
    return hex;
  }

  const r = parseInt(stripped.slice(0, 2), 16);
  const g = parseInt(stripped.slice(2, 4), 16);
  const b = parseInt(stripped.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

function getBaseStyle(
  variant: ButtonVariant,
  isHover: boolean,
  isDisabled: boolean,
  isDark: boolean,
  colors: ReturnType<typeof useCatTheme>["colors"]
): React.CSSProperties {
  switch (variant) {
    case "SolidPrimary": {
      return {
        backgroundColor: isHover ? hexToRgba(colors.components.solid.primary, 0.88) : colors.components.solid.primary,
        color: colors.text.inverse,
        border: "1px solid transparent",
      };
    }
    case "Primary": {
      return {
        backgroundColor: isHover ? hexToRgba(colors.primary, 0.65) : "transparent",
        color: colors.text.primary,
        border: `1px solid ${colors.border.active}`,
      };
    }
    case "Secondary": {
      return {
        backgroundColor: isHover ? hexToRgba(colors.secondary, 0.65) : "transparent",
        color: colors.text.secondary,
        border: `1px solid ${colors.secondary}`,
      };
    }
    case "SolidSecondary": {
      return {
        backgroundColor: isHover ? hexToRgba(colors.secondary, isDark ? 1 : 0.9) : colors.secondary,
        color: colors.text.primary,
        border: "1px solid transparent",
      };
    }
    case "Destructive": {
      return {
        backgroundColor: isHover ? hexToRgba(colors.status.error, 0.82) : colors.status.error,
        color: colors.text.inverse,
        border: "1px solid transparent",
      };
    }
    case "Invisible": {
      return {
        backgroundColor: "transparent",
        color: colors.text.secondary,
        border: "1px solid transparent",
      };
    }
    case "Default":
    default: {
      return {
        backgroundColor: isHover ? hexToRgba(colors.primary, 0.7) : "transparent",
        color: colors.text.primary,
        border: "1px solid transparent",
      };
    }
  }
}

function getSizeStyle(size: ButtonSize, spacing: ReturnType<typeof useCatTheme>["space"]): React.CSSProperties {
  if (size === "link") {
    return {
      padding: 0,
      minHeight: 0,
      borderRadius: 0,
    };
  }

  if (size === "full") {
    return {
      width: "100%",
      padding: `${spacing.s}px ${spacing.m}px`,
      borderRadius: 999,
    };
  }

  if (size === "primary") {
    return {
      padding: `${spacing.s}px ${spacing.l}px`,
      borderRadius: 999,
    };
  }

  return {
    padding: `${spacing.s}px ${spacing.m}px`,
    borderRadius: 999,
  };
}

function ButtonContent({ children, label, leftSlot, rightSlot }: CommonProps) {
  return (
    <>
      {leftSlot}
      {label ? <Text variant="label">{label}</Text> : children}
      {rightSlot}
    </>
  );
}

export function Button({
  children,
  label,
  variant = "Default",
  size = "default",
  style,
  leftSlot,
  rightSlot,
  disabled,
  onMouseEnter,
  onMouseLeave,
  ...props
}: ButtonProps) {
  const t = useCatTheme();
  const [isHover, setIsHover] = useState(false);
  const isDisabled = Boolean(disabled);

  const baseStyle = getBaseStyle(variant, isHover, isDisabled, t.isDark, t.colors);
  const sizeStyle = getSizeStyle(size, t.space);

  return (
    <button
      type="button"
      disabled={isDisabled}
      onMouseEnter={(event) => {
        setIsHover(true);
        onMouseEnter?.(event);
      }}
      onMouseLeave={(event) => {
        setIsHover(false);
        onMouseLeave?.(event);
      }}
      style={{
        display: "inline-flex",
        justifyContent: "center",
        alignItems: "center",
        gap: t.space.xs,
        cursor: isDisabled ? "not-allowed" : "pointer",
        opacity: isDisabled ? 0.6 : 1,
        transition: "all 160ms ease",
        fontFamily: "var(--font-geist-sans), sans-serif",
        fontSize: t.typography.button.fontSize,
        fontWeight: t.typography.button.fontWeight,
        lineHeight: t.typography.button.lineHeight,
        letterSpacing: t.typography.button.letterSpacing,
        textDecoration: "none",
        ...baseStyle,
        ...sizeStyle,
        ...style,
      }}
      {...props}
    >
      <ButtonContent label={label} leftSlot={leftSlot} rightSlot={rightSlot}>
        {children}
      </ButtonContent>
    </button>
  );
}

export function ButtonLink({
  children,
  label,
  href,
  variant = "Default",
  size = "default",
  style,
  leftSlot,
  rightSlot,
  onMouseEnter,
  onMouseLeave,
  ...props
}: ButtonLinkProps) {
  const t = useCatTheme();
  const [isHover, setIsHover] = useState(false);

  const baseStyle = getBaseStyle(variant, isHover, false, t.isDark, t.colors);
  const sizeStyle = getSizeStyle(size, t.space);

  return (
    <Link
      href={href}
      onMouseEnter={(event) => {
        setIsHover(true);
        onMouseEnter?.(event);
      }}
      onMouseLeave={(event) => {
        setIsHover(false);
        onMouseLeave?.(event);
      }}
      style={{
        display: "inline-flex",
        justifyContent: "center",
        alignItems: "center",
        gap: t.space.xs,
        cursor: "pointer",
        transition: "all 160ms ease",
        fontFamily: "var(--font-geist-sans), sans-serif",
        fontSize: t.typography.button.fontSize,
        fontWeight: t.typography.button.fontWeight,
        lineHeight: t.typography.button.lineHeight,
        letterSpacing: t.typography.button.letterSpacing,
        textDecoration: "none",
        ...baseStyle,
        ...sizeStyle,
        ...style,
      }}
      {...props}
    >
      <ButtonContent label={label} leftSlot={leftSlot} rightSlot={rightSlot}>
        {children}
      </ButtonContent>
    </Link>
  );
}
