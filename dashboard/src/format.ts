/** "1.25 s" (at most `digits` decimals, trailing zeros dropped); "?" when unknown. */
export function seconds(s: number | null | undefined, digits = 2): string {
  return s == null ? "?" : `${Number(s.toFixed(digits))} s`;
}
