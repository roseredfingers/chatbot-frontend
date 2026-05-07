/**
 * Build-time configuration for the chatbot SPA.
 * Replace values via fileReplacements (see angular.json production config).
 */
export interface AppEnvironment {
  production: boolean;
  /** Entra (Azure AD) SPA client ID */
  msalClientId: string;
  /** Entra tenant ID (or authority segment) */
  msalTenantId: string;
  /** Must match an Entra “Single-page application” redirect URI */
  redirectUri: string;
  /** Full URL to POST /api/nuvoco_frontend */
  chatApiUrl: string;
  /** Origin + /api — no trailing slash; used for chat_history, prime, append, delete */
  chatHistoryApiUrl: string;
  /** Long-running LLM request budget (ms) */
  chatRequestTimeoutMs: number;
  /** Default HTTP budget for history CRUD (ms) */
  httpDefaultTimeoutMs: number;
  /**
   * Entra sign-in emails that may open `/admin` (must match API `ADMIN_EMAILS`).
   * Leave empty to hide the admin UI from everyone.
   */
  adminEmails?: string[];
  /**
   * When the API has `ADMIN_API_KEY` set, send the same value here.
   */
  adminApiKey?: string;
}
