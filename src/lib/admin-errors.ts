/** Stable error codes of the administrative screen. Contains no server-only imports. */
export const ADMIN_ERRORS = {
  SESSION_EXPIRED: "SESSION_EXPIRED",
  /** The signed-in user is not an owner of the selected organization. Nothing was requested from FastAPI. */
  NOT_OWNER: "ADMIN_NOT_OWNER",
  /** FastAPI refused the request for this organization (membership or role). */
  FORBIDDEN: "ADMIN_FORBIDDEN",
  /** No membership, or the organization does not exist for this user. */
  NOT_FOUND: "ADMIN_NOT_FOUND",
  /** The access could not be recorded in the audit trail, so the report was not delivered. */
  AUDIT_UNAVAILABLE: "AUDIT_UNAVAILABLE",
  /** Any other failure. The screen never shows the cause. */
  UNAVAILABLE: "ADMIN_UNAVAILABLE",
  INVALID_INPUT: "ADMIN_INVALID_INPUT",
} as const;

export type AdminErrorCode = (typeof ADMIN_ERRORS)[keyof typeof ADMIN_ERRORS];

/** The message each code shows. Never a provider detail, a status number or a stack. */
export const ADMIN_MESSAGES: Record<AdminErrorCode, string> = {
  [ADMIN_ERRORS.SESSION_EXPIRED]: "Tu sesión ha caducado. Vuelve a iniciar sesión.",
  [ADMIN_ERRORS.NOT_OWNER]:
    "Solo el owner de una organización puede consultar su consumo y su auditoría. Selecciona otra organización donde seas owner.",
  [ADMIN_ERRORS.FORBIDDEN]: "No tienes permiso para consultar esta organización.",
  [ADMIN_ERRORS.NOT_FOUND]: "Esta organización no está disponible para tu cuenta.",
  [ADMIN_ERRORS.AUDIT_UNAVAILABLE]:
    "No se pudo registrar el acceso a este informe, así que no se muestra. Inténtalo de nuevo en unos minutos.",
  [ADMIN_ERRORS.INVALID_INPUT]: "El periodo o la organización no son válidos.",
  [ADMIN_ERRORS.UNAVAILABLE]:
    "El servicio no está disponible en este momento. Inténtalo de nuevo más tarde.",
};

export function adminMessage(code: string): string {
  return ADMIN_MESSAGES[code as AdminErrorCode] ?? ADMIN_MESSAGES[ADMIN_ERRORS.UNAVAILABLE];
}

/**
 * Maps a failed FastAPI call to an administrative code. Only the status and a stable code are inputs; the body is never
 * read here. A 503 is the audit refusal only when FastAPI names it.
 */
export function mapFastapiFailure(status: number, code: string | null): AdminErrorCode {
  if (status === 401) return ADMIN_ERRORS.SESSION_EXPIRED;
  if (status === 403) return ADMIN_ERRORS.FORBIDDEN;
  if (status === 404) return ADMIN_ERRORS.NOT_FOUND;
  if (status === 422) return ADMIN_ERRORS.INVALID_INPUT;
  if (status === 503 && code === ADMIN_ERRORS.AUDIT_UNAVAILABLE)
    return ADMIN_ERRORS.AUDIT_UNAVAILABLE;
  return ADMIN_ERRORS.UNAVAILABLE;
}
