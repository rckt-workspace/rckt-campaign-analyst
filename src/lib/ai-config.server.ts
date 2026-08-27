/**
 * Configuración central del proveedor de IA (OpenRouter), sólo server-side.
 * La API Key NUNCA tiene valor por defecto ni se registra en logs.
 */

const DEFAULTS = {
  baseUrl: "https://openrouter.ai/api/v1",
  primaryModel: "google/gemma-3-4b-it:free",
  fallbackModel: "openrouter/free",
  useFallback: true,
  temperature: 0.2,
  topP: 0.8,
  maxTokens: 450,
  timeoutMs: 45000,
} as const;

export type AiConfig = {
  apiKey: string;
  baseUrl: string;
  primaryModel: string;
  fallbackModel: string;
  useFallback: boolean;
  temperature: number;
  topP: number;
  maxTokens: number;
  timeoutMs: number;
};

function num(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value.trim());
  return Number.isFinite(parsed) ? parsed : fallback;
}

function bool(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined || value.trim() === "") return fallback;
  return /^(1|true|yes|on)$/i.test(value.trim());
}

function str(value: string | undefined, fallback: string): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed : fallback;
}

/** Lee y valida la configuración. Lanza AI_CONFIG si falta la API Key. */
export function getAiConfig(): AiConfig {
  const apiKey = process.env["OPENROUTER_API_KEY"]?.trim();
  if (!apiKey) {
    console.error("OpenRouter config error: missing OPENROUTER_API_KEY");
    throw new Error("AI_CONFIG");
  }

  return {
    apiKey,
    baseUrl: str(process.env["OPENROUTER_BASE_URL"], DEFAULTS.baseUrl).replace(/\/+$/, ""),
    primaryModel: str(process.env["CHAT_PRIMARY_LLM"], DEFAULTS.primaryModel),
    fallbackModel: str(process.env["CHAT_FALLBACK_LLM"], DEFAULTS.fallbackModel),
    useFallback: bool(process.env["CHAT_USE_FALLBACK"], DEFAULTS.useFallback),
    temperature: num(process.env["CHAT_TEMPERATURE"], DEFAULTS.temperature),
    topP: num(process.env["CHAT_TOP_P"], DEFAULTS.topP),
    maxTokens: num(process.env["CHAT_MAX_TOKENS"], DEFAULTS.maxTokens),
    timeoutMs: num(process.env["CHAT_TIMEOUT_MS"], DEFAULTS.timeoutMs),
  };
}
