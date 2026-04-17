export type ThemeColors = {
  bg: string;
  brand: string;
  primary: string;
  secondary: string;
  components: {
    default: string;
    solid: {
      primary: string;
      secondary: string;
      tertiary: string;
    };
    input: {
      bg: string;
      border: string;
    };
  };
  border: {
    default: string;
    active: string;
  };
  text: {
    primary: string;
    secondary: string;
    muted: string;
    inverse: string;
    active: string;
    brand: string;
  };
  card: {
    primary: string;
    secondary: string;
  };
  container: {
    primary: string;
    secondary: string;
  };
  status: {
    success: string;
    warning: string;
    error: string;
  };
};

export const spacing = {
  xxs: 4,
  xs: 8,
  s: 12,
  m: 16,
  l: 24,
  xl: 32,
  xxl: 48,
} as const;

export const radius = {
  s: 8,
  m: 12,
  l: 16,
  xl: 24,
  xxl: 32,
  full: 999,
} as const;

export const typography = {
  h1: { fontSize: 42, fontWeight: 700 as const, lineHeight: 1.1 },
  h2: { fontSize: 30, fontWeight: 700 as const, lineHeight: 1.15 },
  h3: { fontSize: 22, fontWeight: 600 as const, lineHeight: 1.2 },
  body: { fontSize: 16, fontWeight: 400 as const, lineHeight: 1.5 },
  bodySmall: { fontSize: 14, fontWeight: 400 as const, lineHeight: 1.45 },
  label: { fontSize: 14, fontWeight: 500 as const, lineHeight: 1.2 },
  caption: { fontSize: 12, fontWeight: 500 as const, lineHeight: 1.2 },
  button: { fontSize: 14, fontWeight: 600 as const, lineHeight: 1.2, letterSpacing: 0.2 },
} as const;

export const lightColors: ThemeColors = {
  bg: "#FFFFFF",
  brand: "#D81B60",
  primary: "#F4F4F5",
  secondary: "#E4E4E7",
  components: {
    default: "#FFFFFF",
    solid: {
      primary: "#18181B",
      secondary: "#F4F4F5",
      tertiary: "#FFFFFF",
    },
    input: {
      bg: "#FAFAFA",
      border: "#E4E4E7",
    },
  },
  border: {
    default: "#E4E4E7",
    active: "#A1A1AA",
  },
  text: {
    primary: "#09090B",
    secondary: "#52525B",
    muted: "#A1A1AA",
    inverse: "#FFFFFF",
    active: "#000000",
    brand: "#F084E4",
  },
  card: {
    primary: "#FFFFFF",
    secondary: "#F8FAFC",
  },
  container: {
    primary: "#FFFFFF",
    secondary: "#F4F4F5",
  },
  status: {
    success: "#3FD75B",
    warning: "#FFC107",
    error: "#DC3545",
  },
};

export const darkColors: ThemeColors = {
  bg: "#050505",
  brand: "#D81B60",
  primary: "#18181B",
  secondary: "#27272A",
  components: {
    default: "#18181B",
    solid: {
      primary: "#E0E0E0",
      secondary: "#27272A",
      tertiary: "#09090B",
    },
    input: {
      bg: "#121212",
      border: "#27272A",
    },
  },
  border: {
    default: "#27272A",
    active: "#52525B",
  },
  text: {
    primary: "#FAFAFA",
    secondary: "#A1A1AA",
    muted: "#52525B",
    inverse: "#09090B",
    active: "#FFFFFF",
    brand: "#F084E4",
  },
  card: {
    primary: "#09090B",
    secondary: "#18181B",
  },
  container: {
    primary: "#09090B",
    secondary: "#18181B",
  },
  status: {
    success: "#3FD75B",
    warning: "#FFC107",
    error: "#DC3545",
  },
};
