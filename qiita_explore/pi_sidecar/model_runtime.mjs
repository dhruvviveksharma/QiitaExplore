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
async function fetchNrpModels({ flaskUrl, piSecret }) {
  const res = await fetch(`${flaskUrl}/api/internal/models`, {
    headers: { "x-pi-secret": piSecret },
  });
  if (!res.ok) {
    throw new Error(`failed to fetch model roster: ${res.status} ${await res.text()}`);
  }
  const { models } = await res.json();
  if (!models?.length) throw new Error("Flask returned an empty model roster");
  return models;
}

// Anthropic models pass straight through to pi's built-in provider, which reads
// ANTHROPIC_API_KEY from the environment on its own — no registration needed here.
export const ANTHROPIC_PROVIDER_ID = "anthropic";

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
