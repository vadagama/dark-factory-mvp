/**
 * UI-gate self-test of the Small UIKit (T-042, ADR-014): proves that the
 * shipped policies actually BLOCK violations instead of merely declaring
 * them. Runs programmatically (ESLint and stylelint APIs, no shell):
 *
 * 1. The product ESLint policy (policy/eslint-small-ui.mjs) against JSX
 *    fixtures — deep `@small/ui/*` imports and direct `@radix-ui/*` imports
 *    must be reported, the clean root import must pass.
 * 2. The kit's own policy (eslint.config.js) against in-memory sources placed
 *    (virtually) outside/inside `src/components/**` — Radix must be blocked
 *    outside components and allowed inside.
 * 3. The stylelint token discipline (stylelint.config.js) against CSS
 *    fixtures — hex and named colors must be reported, `var(--small-*)` must
 *    pass.
 *
 * Exit code is 0 only when every bad fixture is blocked and every clean
 * fixture passes; this script is the `npm run test:gates` CI step.
 */
import { ESLint } from "eslint";
import stylelint from "stylelint";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const POLICY = join(ROOT, "policy", "eslint-small-ui.mjs");
const KIT_CONFIG = join(ROOT, "eslint.config.js");

/** @type {{ file: string, blocked: boolean, description: string }[]} */
const POLICY_FIXTURES = [
  { file: "policy/fixtures/clean.tsx", blocked: false, description: "root import of @small/ui passes" },
  {
    file: "policy/fixtures/violation-deep-import.tsx",
    blocked: true,
    description: "deep import @small/ui/internal is blocked",
  },
  {
    file: "policy/fixtures/violation-radix-import.tsx",
    blocked: true,
    description: "direct import @radix-ui/react-dialog is blocked",
  },
];

const KIT_POLICY_CASES = [
  {
    filePath: "src/patterns/__gate-fixture.tsx",
    code: 'import * as DialogPrimitive from "@radix-ui/react-dialog";\n\nexport const R = DialogPrimitive.Root;\n',
    blocked: true,
    description: "kit policy blocks Radix outside src/components/**",
  },
  {
    filePath: "src/components/__gate-fixture.tsx",
    code: 'import * as DialogPrimitive from "@radix-ui/react-dialog";\n\nexport const R = DialogPrimitive.Root;\n',
    blocked: false,
    description: "kit policy allows Radix inside src/components/**",
  },
  {
    filePath: "src/patterns/__gate-fixture.tsx",
    code: 'import { Button } from "@small/ui/internal";\n\nexport const B = Button;\n',
    blocked: true,
    description: "kit policy blocks deep self-imports",
  },
];

/** @type {{ file: string, blocked: boolean, description: string }[]} */
const STYLELINT_FIXTURES = [
  { file: "policy/fixtures/clean.css", blocked: false, description: "var(--small-*) colors pass" },
  { file: "policy/fixtures/violation-hex.css", blocked: true, description: "hex color is blocked" },
  { file: "policy/fixtures/violation-raw-color.css", blocked: true, description: "named color is blocked" },
];

let failures = 0;

/** @param {string} name @param {boolean} ok @param {string} detail */
function report(name, ok, detail) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name} — ${detail}`);
  if (!ok) failures += 1;
}

// 1. Product policy against fixture files.
const policyEslint = new ESLint({ cwd: ROOT, overrideConfigFile: POLICY });
for (const { file, blocked, description } of POLICY_FIXTURES) {
  const results = await policyEslint.lintFiles([file]);
  const errors = results.reduce((count, result) => count + result.errorCount, 0);
  const rules = [
    ...new Set(results.flatMap((result) => result.messages.map((message) => message.ruleId))),
  ];
  const ok = blocked ? errors > 0 && rules.includes("no-restricted-imports") : errors === 0;
  report(`eslint ${file}`, ok, blocked ? `${description} (${errors} error(s))` : description);
}

// 2. Kit policy against in-memory sources (virtual file paths).
const kitEslint = new ESLint({ cwd: ROOT, overrideConfigFile: KIT_CONFIG });
for (const { filePath, code, blocked, description } of KIT_POLICY_CASES) {
  const results = await kitEslint.lintText(code, { filePath: join(ROOT, filePath) });
  const errors = results.reduce((count, result) => count + result.errorCount, 0);
  const ok = blocked ? errors > 0 : errors === 0;
  report(`eslint ${filePath} (virtual)`, ok, blocked ? `${description} (${errors} error(s))` : description);
}

// 3. Stylelint token discipline against fixture files.
const stylelintResult = await stylelint.lint({
  configFile: join(ROOT, "stylelint.config.js"),
  files: STYLELINT_FIXTURES.map(({ file }) => join(ROOT, file)),
  cwd: ROOT,
});
const warningsByFile = new Map(
  stylelintResult.results.map((result) => [result.source ?? "", result.warnings]),
);
for (const { file, blocked, description } of STYLELINT_FIXTURES) {
  const warnings = warningsByFile.get(join(ROOT, file)) ?? [];
  const ok = blocked ? warnings.length > 0 : warnings.length === 0;
  report(
    `stylelint ${file}`,
    ok,
    blocked ? `${description} (${warnings.length} warning(s))` : description,
  );
}

if (failures > 0) {
  console.error(`\nui kit gates: ${failures} gate self-check(s) FAILED`);
  process.exitCode = 1;
} else {
  console.log("\nui kit gates: all fixtures behave as declared (violations blocked, clean code passes)");
}
