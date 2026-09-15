/**
 * Synthetic API fixtures for the Playwright smoke suite (ADR-021 p.4):
 * clearly fake data shaped like the T035 wire contract, served by
 * `page.route` — no real API and no secrets are involved in e2e runs.
 */

import type { Page } from "@playwright/test";
import type {
  Change,
  ChangeCard,
  ChangeTrace,
  Decision,
  Evidence,
  Finding,
  GateResult,
  RunCard,
  RunSummary,
} from "../../src/api/types";

const change1: Change = {
  id: "chg_demo_001",
  title: "Add /health endpoint",
  description: "Liveness probe for the demo service",
  source: "console",
  external_ref: null,
  product: { provider: "github", slug: "acme/demo-service" },
  risk_class: "R2",
  change_request: null,
  created_at: "2026-09-01T10:00:00Z",
};

const change2: Change = {
  id: "chg_demo_002",
  title: "Split legacy billing module",
  description: null,
  source: "cli",
  external_ref: "ACME-4242",
  product: { provider: "gitlab", slug: "acme/legacy-api" },
  risk_class: "R3",
  change_request: null,
  created_at: "2026-09-01T09:00:00Z",
};

const run1Summary: RunSummary = {
  run_id: "run_demo_001",
  change_id: "chg_demo_001",
  route: "standard",
  provider: "github",
  status: "succeeded",
  state_revision: 2,
  created_at: "2026-09-01T10:01:00Z",
  updated_at: "2026-09-01T10:15:00Z",
  finished_at: "2026-09-01T10:15:00Z",
};

const run2Summary: RunSummary = {
  run_id: "run_demo_002",
  change_id: "chg_demo_002",
  route: "standard",
  provider: "gitlab",
  status: "running",
  state_revision: 1,
  created_at: "2026-09-01T10:20:00Z",
  updated_at: "2026-09-01T10:25:00Z",
  finished_at: null,
};

const run1Card: RunCard = {
  ...run1Summary,
  stages: [
    { stage: "specification", status: "succeeded", input_revision: null, attempt_count: 1 },
    { stage: "planning", status: "succeeded", input_revision: "abc1234", attempt_count: 1 },
    { stage: "construction", status: "succeeded", input_revision: "abc1234", attempt_count: 2 },
    { stage: "review_verification", status: "succeeded", input_revision: "def5678", attempt_count: 1 },
    { stage: "release", status: "succeeded", input_revision: "def5678", attempt_count: 1 },
  ],
  usage: { prompt_tokens: 12500, completion_tokens: 4300, cost: "0.2847", manual_interventions: 1 },
  gates: [
    { gate: "review", status: "passed", sha: "abc1234def", summary: "2 approvals", evidence_ids: ["ev_001"] },
  ],
  open_blockers: 1,
};

const run2Card: RunCard = {
  ...run2Summary,
  stages: [
    { stage: "specification", status: "succeeded", input_revision: null, attempt_count: 1 },
    { stage: "planning", status: "in_progress", input_revision: "abc1234", attempt_count: 1 },
  ],
  usage: { prompt_tokens: 900, completion_tokens: 100, cost: null, manual_interventions: 0 },
  gates: [
    { gate: "specification", status: "passed", sha: null, summary: null, evidence_ids: [] },
  ],
  open_blockers: 0,
};

const card1: ChangeCard = {
  ...change1,
  runs: [{ run_id: "run_demo_001", status: "succeeded" }],
  decisions_count: 1,
};

const card2: ChangeCard = {
  ...change2,
  runs: [{ run_id: "run_demo_002", status: "running" }],
  decisions_count: 0,
};

const trace1: ChangeTrace = {
  change_id: "chg_demo_001",
  runs: [
    {
      change_id: "chg_demo_001",
      run_id: "run_demo_001",
      status: "succeeded",
      chain: [
        { stage: "specification", status: "succeeded", input_revision: null, attempt_number: 1, produced_at: "2026-09-01T10:03:00Z", artifacts: [] },
        { stage: "planning", status: "succeeded", input_revision: "abc1234", attempt_number: 1, produced_at: "2026-09-01T10:06:00Z", artifacts: [] },
        { stage: "construction", status: "succeeded", input_revision: "abc1234", attempt_number: 2, produced_at: "2026-09-01T10:10:00Z", artifacts: [] },
        { stage: "review_verification", status: "succeeded", input_revision: "def5678", attempt_number: 1, produced_at: "2026-09-01T10:13:00Z", artifacts: [] },
        { stage: "release", status: "succeeded", input_revision: "def5678", attempt_number: 1, produced_at: "2026-09-01T10:15:00Z", artifacts: [] },
      ],
    },
  ],
};

const trace2: ChangeTrace = {
  change_id: "chg_demo_002",
  runs: [
    {
      change_id: "chg_demo_002",
      run_id: "run_demo_002",
      status: "running",
      chain: [
        { stage: "specification", status: "succeeded", input_revision: null, attempt_number: 1, produced_at: "2026-09-01T10:22:00Z", artifacts: [] },
        { stage: "planning", status: "in_progress", input_revision: "abc1234", attempt_number: 1, produced_at: "2026-09-01T10:25:00Z", artifacts: [] },
      ],
    },
  ],
};

const approval1: Decision = {
  id: "dec_abc123",
  gate: "review",
  outcome: "approved",
  decided_by: "human",
  role: null,
  decided_at: "2026-09-01T11:00:00Z",
  commit_sha: "abc1234def",
  comment: "ok",
  evidence_ids: [],
};

/** Response for a recorded approval in the smoke suite. */
const newApproval: Decision = {
  id: "dec_new001",
  gate: "review",
  outcome: "approved",
  decided_by: "human",
  role: null,
  decided_at: "2026-09-01T12:00:00Z",
  commit_sha: "deadbeef1234",
  comment: null,
  evidence_ids: [],
};

const gate1: GateResult[] = [
  { gate: "review", status: "passed", sha: "abc1234def", summary: "2 approvals", evidence_ids: ["ev_001"] },
];

const gate2: GateResult[] = [
  { gate: "specification", status: "passed", sha: null, summary: null, evidence_ids: [] },
];

const blocker1: Finding = {
  id: "fnd_001",
  origin: "agent",
  role: "quality",
  severity: "blocker",
  category: "tests",
  file: "src/api.py",
  line: 42,
  reviewed_sha: "abc1234",
  required_action: "Fix failing integration tests",
  status: "open",
  confidence: 0.9,
  evidence_ids: [],
};

const evidence1: Evidence = {
  id: "ev_001",
  type: "test_results",
  uri: "https://ci.example.com/jobs/1",
  checksum: null,
  produced_at: "2026-09-01T10:14:00Z",
  required: true,
  available: true,
};

function problem(status: number, title: string, detail: string): object {
  return { type: "about:blank", title, status, detail };
}

/** Recorded POST requests, for assertions in the specs. */
export interface CapturedPost {
  pathname: string;
  body: unknown;
  headers: Record<string, string>;
}

// Routes every request under the api/v1 path prefix to the synthetic
// dataset. Returns the capture list of POSTs made by the app under test.
export async function stubApi(page: Page): Promise<{ posts: CapturedPost[] }> {
  const posts: CapturedPost[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const { pathname } = new URL(request.url());
    const method = request.method();

    if (method === "POST") {
      posts.push({
        pathname,
        body: request.postDataJSON(),
        headers: request.headers(),
      });
      if (pathname === "/api/v1/changes") {
        await route.fulfill({ status: 201, json: request.postDataJSON() });
        return;
      }
      if (pathname === "/api/v1/changes/chg_demo_001/approvals") {
        await route.fulfill({ status: 201, json: newApproval });
        return;
      }
      await route.fulfill({ status: 404, json: problem(404, "Not Found", `unmatched POST ${pathname}`) });
      return;
    }

    const getRoutes: Record<string, unknown> = {
      "/api/v1/changes": [change1, change2],
      "/api/v1/changes/chg_demo_001": card1,
      "/api/v1/changes/chg_demo_002": card2,
      "/api/v1/changes/chg_demo_001/trace": trace1,
      "/api/v1/changes/chg_demo_002/trace": trace2,
      "/api/v1/changes/chg_demo_001/approvals": [approval1],
      "/api/v1/changes/chg_demo_002/approvals": [],
      "/api/v1/runs": [run1Summary, run2Summary],
      "/api/v1/runs/run_demo_001": run1Card,
      "/api/v1/runs/run_demo_002": run2Card,
      "/api/v1/runs/run_demo_001/gates": gate1,
      "/api/v1/runs/run_demo_002/gates": gate2,
      "/api/v1/runs/run_demo_001/findings": [blocker1],
      "/api/v1/runs/run_demo_002/findings": [],
      "/api/v1/runs/run_demo_001/evidence": [evidence1],
      "/api/v1/runs/run_demo_002/evidence": [],
    };
    const body = getRoutes[pathname];
    if (body === undefined) {
      await route.fulfill({ status: 404, json: problem(404, "Not Found", `unmatched GET ${pathname}`) });
      return;
    }
    await route.fulfill({ status: 200, json: body });
  });
  return { posts };
}

export const E2E_TOKEN = "synthetic-e2e-operator-token";
export const TOKEN_STORAGE_KEY = "dark_factory_operator_token";
