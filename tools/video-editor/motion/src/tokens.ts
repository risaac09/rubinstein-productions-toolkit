// Token names and placeholder values shared by every composition. Real values
// come from the private brand context (production/design-tokens.md) through a
// `tokens` prop; the placeholders below are not brand values.
export type TokenName =
  | "color.marrow"
  | "color.amber"
  | "color.bone"
  | "color.text-on-dark"
  | "type.display"
  | "type.body";

export type Tokens = Partial<Record<TokenName, string>>;

export const FALLBACK: Record<TokenName, string> = {
  "color.marrow": "#0f0f0f",
  "color.amber": "#e8491d",
  "color.bone": "#e9e4d8",
  "color.text-on-dark": "#ffffff",
  "type.display": "Helvetica, Arial, sans-serif",
  "type.body": "Helvetica, Arial, sans-serif",
};

// A token that is missing, null or empty in the props JSON keeps its fallback.
export const resolveTokens = (tokens?: Tokens): Record<TokenName, string> => {
  const t = { ...FALLBACK };
  for (const [k, v] of Object.entries(tokens ?? {})) {
    if (k in FALLBACK && typeof v === "string" && v !== "") t[k as TokenName] = v;
  }
  return t;
};
