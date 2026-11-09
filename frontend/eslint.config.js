import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Browser-security rules (security model C19). Everything that comes from the API is
// untrusted and must be rendered as React text; the production CSP forbids inline styles.
const xssRules = [
  {
    selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
    message: "dangerouslySetInnerHTML is forbidden: render API data as text.",
  },
  {
    selector: "Property[key.name='dangerouslySetInnerHTML']",
    message: "dangerouslySetInnerHTML is forbidden: render API data as text.",
  },
  {
    selector: "JSXAttribute[name.name='style']",
    message: "Inline style attributes violate the CSP (style-src 'self'): use a CSS class or SVG attributes.",
  },
  {
    selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]",
    message: "Writing HTML is forbidden: render API data as text.",
  },
  {
    selector: "CallExpression[callee.property.name=/^(insertAdjacentHTML|createContextualFragment|write|writeln)$/]",
    message: "Writing HTML is forbidden: render API data as text.",
  },
];

const scriptUrlRule = {
  selector: "Literal[value=/^\\s*javascript:/i]",
  message: "javascript: URLs are forbidden; links from data go through safeUrl().",
};

export default tseslint.config(
  { ignores: ["dist", "node_modules", "coverage"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "no-restricted-syntax": ["error", ...xssRules, scriptUrlRule],
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Nothing is stored client-side (no tokens, no secrets)." },
        { name: "sessionStorage", message: "Nothing is stored client-side (no tokens, no secrets)." },
        { name: "eval", message: "eval is forbidden (CSP has no unsafe-eval)." },
      ],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "localStorage", message: "Nothing is stored client-side." },
        { object: "window", property: "sessionStorage", message: "Nothing is stored client-side." },
      ],
      "no-implied-eval": "error",
      "no-new-func": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  {
    // Tests feed hostile strings (javascript: URLs, markup) on purpose.
    files: ["src/**/*.test.{ts,tsx}", "src/test/**"],
    rules: { "no-restricted-syntax": ["error", ...xssRules] },
  },
  {
    files: ["*.config.{js,ts}"],
    languageOptions: { globals: globals.node },
  },
);
