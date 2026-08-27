/**
 * Capa única de acceso a OpenRouter (API compatible con OpenAI).
 * Responsabilidad exclusiva: ejecutar solicitudes de chat completions.
 */
import { getAiConfig, type AiConfig } from "./ai-config.server";

export type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

const RECOVERABLE_STATUS = new Set([408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524]);

class RecoverableAiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RecoverableAiError";
  }
}

async function callModel(
  config: AiConfig,
  model: string,
  messages: ChatMessage[],
  externalSignal?: AbortSignal,
): Promise<string> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.timeoutMs);
  const onAbort = () => controller.abort();
  externalSignal?.addEventListener("abort", onAbort);
  const startedAt = Date.now();

  try {
    const response = await fetch(`${config.baseUrl}/chat/completions`, {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.apiKey}`,
        "X-Title": "RCKT Campaign Analyst",
      },
      body: JSON.stringify({
        model,
        messages,
        temperature: config.temperature,
        top_p: config.topP,
        max_tokens: config.maxTokens,
        stream: false,
      }),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      console.error(
        `OpenRouter request failed model=${model} status=${response.status} ms=${Date.now() - startedAt} detail=${detail.slice(0, 300)}`,
      );
      // 404 y 400 de "modelo inválido/no disponible" se tratan como recuperables
      // para permitir el único intento con el modelo de fallback.
      const modelUnavailable =
        response.status === 404 ||
        (response.status === 400 && /valid model|model.*(not found|unavailable)/i.test(detail));
      if (RECOVERABLE_STATUS.has(response.status) || modelUnavailable) {
        throw new RecoverableAiError(`AI_${response.status}`);
      }
      throw new Error(`AI_${response.status}`);
    }

    const payload = (await response.json()) as {
      choices?: { message?: { content?: string } }[];
      error?: { message?: string };
    };
    const text = payload.choices?.[0]?.message?.content?.trim() ?? "";
    if (!text) {
      console.error(
        `OpenRouter empty response model=${model} ms=${Date.now() - startedAt} error=${payload.error?.message ?? "none"}`,
      );
      throw new RecoverableAiError("AI_EMPTY");
    }

    console.log(`OpenRouter ok model=${model} ms=${Date.now() - startedAt}`);
    return text;
  } catch (error) {
    if (error instanceof RecoverableAiError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      console.error(`OpenRouter timeout model=${model} ms=${Date.now() - startedAt}`);
      throw new RecoverableAiError("AI_TIMEOUT");
    }
    if (error instanceof Error && error.message.startsWith("AI_")) throw error;
    console.error(`OpenRouter network error model=${model} ms=${Date.now() - startedAt}`, error);
    throw new RecoverableAiError("AI_NETWORK");
  } finally {
    clearTimeout(timeout);
    externalSignal?.removeEventListener("abort", onAbort);
  }
}

/**
 * Ejecuta primary y, ante un error recuperable (timeout, rate limit, 5xx,
 * modelo no disponible), realiza UN único intento con el modelo de fallback.
 */
export async function runChatCompletion(
  messages: ChatMessage[],
  signal?: AbortSignal,
): Promise<{ text: string; model: string; usedFallback: boolean }> {
  const config = getAiConfig();

  try {
    const text = await callModel(config, config.primaryModel, messages, signal);
    return { text, model: config.primaryModel, usedFallback: false };
  } catch (error) {
    const recoverable = error instanceof RecoverableAiError;
    if (!recoverable || !config.useFallback || config.fallbackModel === config.primaryModel) {
      throw error instanceof Error ? error : new Error("AI_UNKNOWN");
    }
    console.warn(
      `OpenRouter primary failed (${(error as Error).message}); trying fallback=${config.fallbackModel}`,
    );
    const text = await callModel(config, config.fallbackModel, messages, signal);
    return { text, model: config.fallbackModel, usedFallback: true };
  }
}
