import path from "node:path";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";

const NRP_PROVIDER_ID = "nrp";
const NRP_BASE_URL = process.env.NRP_BASE_URL || "https://ellm.nrp-nautilus.io/v1";

/**
 * Fetch the model roster from Flask — the same single-source-of-truth pattern
 * the tool schemas already use. Not hardcoded here: an earlier hardcoded copy
 * had already drifted from config.py MODEL_METADATA at introduction (kimi and
 * glm-5 both listed short), and since pi uses contextWindow to decide when to
 * compact, a wrong number silently compacts conversations early rather than
 * erroring.
 *
 * Flask filters on supports_tools, which matters: pi has no per-model tool flag
 * and always sends tool schemas, so a model that cannot do tool calling must
 * never appear in this list.
 */
const ROSTER_RETRY_MS = 3000;
const ROSTER_TIMEOUT_MS = 30000;

async function fetchNrpModels({ flaskUrl, piSecret }) {
  // Retried, not fail-fast: Flask may still be booting. start_barnacle.sh
  // already waits for it before launching the sidecar, but start_sidecar.sh is
  // also a standalone entry point and a future systemd unit gives no ordering
  // guarantee — without this, a sidecar that starts a second too early dies for
  // good. Each attempt is logged so a genuinely wrong secret (a steady 401)
  // reads differently from a slow start.
  const deadline = Date.now() + ROSTER_TIMEOUT_MS;
  let attempt = 0;
  for (;;) {
    attempt += 1;
    let reason;
    try {
      const res = await fetch(`${flaskUrl}/api/internal/models`, {
        headers: { "x-pi-secret": piSecret },
      });
      if (res.ok) {
        const { models } = await res.json();
        if (models?.length) return models;
        reason = "Flask returned an empty model roster";
      } else {
        reason = `HTTP ${res.status} ${(await res.text()).slice(0, 120)}`;
      }
    } catch (err) {
      reason = err.message; // connection refused while Flask is still binding
    }

    if (Date.now() + ROSTER_RETRY_MS >= deadline) {
      throw new Error(
        `failed to fetch model roster from ${flaskUrl} after ${attempt} attempts: ${reason}`
      );
    }
    console.log(`waiting for Flask model roster (attempt ${attempt}): ${reason}`);
    await new Promise((r) => setTimeout(r, ROSTER_RETRY_MS));
  }
}

// Anthropic models pass straight through to pi's built-in provider, which reads
// ANTHROPIC_API_KEY from the environment on its own — no registration needed
// here. Flask (helpers/pi_client.py) sends "anthropic/<model>" for those, so
// resolveModel's own provider-prefix split below already routes it correctly.

/**
 * Build a ModelRuntime isolated to the sidecar's own agentDir (never touches
 * ~/.pi/agent on the host) and register the NRP OpenAI-compatible provider.
 */
export async function createModelRuntime({ agentDir, flaskUrl, piSecret }) {
  const runtime = await ModelRuntime.create({
    authPath: path.join(agentDir, "auth.json"),
    modelsPath: null, // no external models.json — providers are registered programmatically
    allowModelNetwork: false,
  });

  const apiKey = process.env.OPENAI_API_KEY || process.env.API_KEY;
  if (apiKey) {
    const models = await fetchNrpModels({ flaskUrl, piSecret });
    runtime.registerProvider(NRP_PROVIDER_ID, {
      name: "NRP Nautilus ELLM",
      baseUrl: NRP_BASE_URL,
      apiKey,
      api: "openai-completions",
      compat: {
        supportsDeveloperRole: false,
        supportsReasoningEffort: false,
        maxTokensField: "max_tokens",
      },
      models: models.map((m) => ({
        id: m.id,
        name: m.id,
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0 },
        contextWindow: m.contextWindow,
        maxTokens: 8192,
      })),
    });
  }

  return runtime;
}

/**
 * Resolve a "provider/modelId" or bare modelId string (defaults to the NRP
 * provider) to a pi Model object. Throws with a message safe to surface to
 * the caller — never lets an unresolved model reach createAgentSession.
 */
export function resolveModel(runtime, modelSpec) {
  const [provider, modelId] = modelSpec.includes("/")
    ? modelSpec.split(/\/(.*)/s).slice(0, 2)
    : [NRP_PROVIDER_ID, modelSpec];
  const model = runtime.getModel(provider, modelId);
  if (!model) {
    throw new Error(`Unknown model "${modelSpec}" (resolved provider=${provider}, id=${modelId})`);
  }
  return model;
}
