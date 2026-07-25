import path from "node:path";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";

const NRP_PROVIDER_ID = "nrp";
const NRP_BASE_URL = process.env.NRP_BASE_URL || "https://ellm.nrp-nautilus.io/v1";

// Mirrors qiita_explore/backend/config.py MODEL_METADATA — keep contextWindow in
// sync by hand when that file changes. gemma-small is intentionally absent: pi has
// no per-model "supports tools" flag and always sends tool schemas, so a model that
// can't do tool calling (config.py: supports_tools=False) must never be offered here.
const NRP_MODELS = [
  { id: "qwen3", name: "Qwen3", contextWindow: 1_000_000 },
  { id: "qwen3-small", name: "Qwen3 Small", contextWindow: 1_000_000 },
  { id: "gpt-oss", name: "GPT-OSS", contextWindow: 131_072 },
  { id: "gemma", name: "Gemma", contextWindow: 262_144 },
  { id: "kimi", name: "Kimi", contextWindow: 131_072 },
  { id: "glm-5", name: "GLM-5", contextWindow: 131_072 },
  { id: "minimax-m2", name: "MiniMax M2", contextWindow: 204_800 },
];

// Anthropic models pass straight through to pi's built-in provider, which reads
// ANTHROPIC_API_KEY from the environment on its own — no registration needed here.
export const ANTHROPIC_PROVIDER_ID = "anthropic";

/**
 * Build a ModelRuntime isolated to the sidecar's own agentDir (never touches
 * ~/.pi/agent on the host) and register the NRP OpenAI-compatible provider.
 */
export async function createModelRuntime({ agentDir }) {
  const runtime = await ModelRuntime.create({
    authPath: path.join(agentDir, "auth.json"),
    modelsPath: null, // no external models.json — providers are registered programmatically
    allowModelNetwork: false,
  });

  const apiKey = process.env.OPENAI_API_KEY || process.env.API_KEY;
  if (apiKey) {
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
      models: NRP_MODELS.map((m) => ({
        id: m.id,
        name: m.name,
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
