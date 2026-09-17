/**
 * The Mantine theme: how the shared objects of web/src/design/DESIGN.md reach every Mantine component in one place.
 *
 * - Numbers (spacing, corners, type, layers) resolve to the tokens in tokens.css, colours to the faces in faces.css,
 *   so a token edit moves the whole dashboard.
 * - `variantColorResolver` is the one colour law for Button, Badge, ActionIcon, Alert and friends: accent for the one
 *   thing to press and for chosen things, green/amber/red for the three meanings, plain for everything else. Mantine
 *   colour names used by pages (teal, yellow, red, ...) map onto those meanings, so no page picks a colour of its own.
 * - Shapes, the emboss, the hover rise and the table/field/chip dress live in `objects/*.css`, one file per object.
 */
import {
  type CSSVariablesResolver,
  type MantineColorsTuple,
  type VariantColorsResolver,
  createTheme,
  defaultVariantColorsResolver,
} from '@mantine/core';

type Meaning = 'accent' | 'ok' | 'warn' | 'bad' | 'plain';

const MEANING_BY_COLOR: Record<string, Meaning> = {
  accent: 'accent',
  blue: 'accent',
  indigo: 'accent',
  cyan: 'accent',
  violet: 'accent',
  grape: 'accent',
  teal: 'ok',
  green: 'ok',
  lime: 'ok',
  yellow: 'warn',
  orange: 'warn',
  red: 'bad',
  pink: 'bad',
  gray: 'plain',
  dark: 'plain',
};

/** The meaning a page's colour name stands for (unknown names read as plain). */
export function meaningOf(color: string | undefined): Meaning {
  if (!color) return 'accent';
  const base = color.split('.')[0] ?? color;
  return MEANING_BY_COLOR[base] ?? 'plain';
}

const INK: Record<Meaning, string> = {
  accent: 'var(--sed-accent-text)',
  ok: 'var(--sed-ok)',
  warn: 'var(--sed-warn)',
  bad: 'var(--sed-bad)',
  plain: 'var(--sed-tx-1)',
};

const WEAK: Record<Meaning, string> = {
  accent: 'var(--sed-accent-weak)',
  ok: 'var(--sed-ok-weak)',
  warn: 'var(--sed-warn-weak)',
  bad: 'var(--sed-bad-weak)',
  plain: 'var(--sed-plain-weak)',
};

const variantColorResolver: VariantColorsResolver = (input) => {
  const meaning = meaningOf(input.color);
  const hairline = 'var(--sed-hairline) solid var(--sed-line)';
  switch (input.variant) {
    case 'filled':
      if (meaning === 'bad') {
        return { background: 'var(--sed-bad)', hover: 'var(--sed-bad)', color: 'var(--sed-accent-ink)', border: hairline };
      }
      return {
        background: 'var(--sed-accent)',
        hover: 'var(--sed-accent)',
        color: 'var(--sed-accent-ink)',
        border: 'var(--sed-hairline) solid transparent',
      };
    case 'light':
    case 'dot':
      return { background: WEAK[meaning], hover: WEAK[meaning], color: INK[meaning], border: hairline };
    case 'outline':
      return { background: 'transparent', hover: 'var(--sed-row-hover)', color: INK[meaning], border: hairline };
    case 'subtle':
    case 'transparent':
      return { background: 'transparent', hover: 'var(--sed-row-hover)', color: INK[meaning], border: 'none' };
    case 'default':
      return { background: 'var(--sed-panel)', hover: 'var(--sed-panel)', color: 'var(--sed-tx-1)', border: hairline };
    default:
      return defaultVariantColorsResolver(input);
  }
};

const cssVariablesResolver: CSSVariablesResolver = () => {
  const shared = {
    '--mantine-color-body': 'var(--sed-canvas)',
    '--mantine-color-text': 'var(--sed-tx)',
    '--mantine-color-dimmed': 'var(--sed-tx-2)',
    '--mantine-color-default': 'var(--sed-panel)',
    '--mantine-color-default-hover': 'var(--sed-row-hover)',
    '--mantine-color-default-color': 'var(--sed-tx-1)',
    '--mantine-color-default-border': 'var(--sed-line)',
    '--mantine-color-placeholder': 'var(--sed-tx-3)',
    '--mantine-color-anchor': 'var(--sed-accent-text)',
    '--mantine-color-error': 'var(--sed-bad)',
    '--mantine-color-bright': 'var(--sed-tx)',
    '--mantine-primary-color-filled': 'var(--sed-accent)',
    '--mantine-primary-color-filled-hover': 'var(--sed-accent)',
    '--mantine-primary-color-light': 'var(--sed-accent-weak)',
    '--mantine-primary-color-light-hover': 'var(--sed-accent-weak)',
    '--mantine-primary-color-light-color': 'var(--sed-accent-text)',
    '--mantine-primary-color-contrast': 'var(--sed-accent-ink)',
  };
  return {
    variables: {
      '--mantine-z-index-app': 'var(--sed-z-content)',
      '--mantine-z-index-modal': 'var(--sed-z-dialog)',
      '--mantine-z-index-popover': 'var(--sed-z-menu)',
      '--mantine-z-index-overlay': 'var(--sed-z-dialog)',
      '--mantine-z-index-max': 'var(--sed-z-tooltip)',
    },
    light: shared,
    dark: shared,
  };
};

/** Mantine needs ten shades per colour; the accent is one colour, so every shade is the accent. */
const accent = Array.from({ length: 10 }, () => 'var(--sed-accent)') as unknown as MantineColorsTuple;

export const theme = createTheme({
  primaryColor: 'accent',
  primaryShade: 6,
  colors: { accent },
  autoContrast: false,
  fontFamily: 'var(--sed-font)',
  fontFamilyMonospace: 'var(--sed-font-mono)',
  headings: {
    fontFamily: 'var(--sed-font)',
    fontWeight: 'var(--sed-weight-title)',
    sizes: {
      h1: { fontSize: 'var(--sed-fs-title)', lineHeight: 'var(--sed-lh-tight)' },
      h2: { fontSize: 'var(--sed-fs-heading)', lineHeight: 'var(--sed-lh)' },
      h3: { fontSize: 'var(--sed-fs-heading)', lineHeight: 'var(--sed-lh)' },
      h4: { fontSize: 'var(--sed-fs-body)', lineHeight: 'var(--sed-lh)' },
      h5: { fontSize: 'var(--sed-fs-body)', lineHeight: 'var(--sed-lh)' },
      h6: { fontSize: 'var(--sed-fs-tiny)', lineHeight: 'var(--sed-lh)' },
    },
  },
  fontSizes: {
    xs: 'var(--sed-fs-tiny)',
    sm: 'var(--sed-fs-body)',
    md: 'var(--sed-fs-body)',
    lg: 'var(--sed-fs-heading)',
    xl: 'var(--sed-fs-heading)',
  },
  lineHeights: { xs: 'var(--sed-lh)', sm: 'var(--sed-lh)', md: 'var(--sed-lh)', lg: 'var(--sed-lh)', xl: 'var(--sed-lh)' },
  spacing: {
    xs: 'var(--sed-s1)',
    sm: 'var(--sed-s2)',
    md: 'var(--sed-s3)',
    lg: 'var(--sed-s4)',
    xl: 'var(--sed-s5)',
  },
  radius: { xs: 'var(--sed-r1)', sm: 'var(--sed-r1)', md: 'var(--sed-r1)', lg: 'var(--sed-r1)', xl: 'var(--sed-r1)' },
  defaultRadius: 'md',
  shadows: {
    xs: 'var(--sed-emboss)',
    sm: 'var(--sed-emboss)',
    md: 'var(--sed-shadow-deep)',
    lg: 'var(--sed-shadow-deep)',
    xl: 'var(--sed-shadow-deep)',
  },
  variantColorResolver,
  components: {
    Badge: { defaultProps: { radius: 'md' } },
    Card: { defaultProps: { withBorder: true, radius: 'md' } },
    Paper: { defaultProps: { radius: 'md' } },
    Modal: { defaultProps: { centered: true } },
    Tooltip: { defaultProps: { withArrow: true } },
    Menu: { defaultProps: { shadow: 'md' } },
    Popover: { defaultProps: { shadow: 'md' } },
    HoverCard: { defaultProps: { shadow: 'md' } },
    Combobox: { defaultProps: { shadow: 'md' } },
  },
});

export { cssVariablesResolver };
