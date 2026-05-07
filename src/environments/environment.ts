import type { AppEnvironment } from './environment.interface';

/**
 * Local development — replace placeholders or use Angular `fileReplacements`
 * with a gitignored `environment.local.ts` pattern if you prefer not to edit this file.
 */
export const environment: AppEnvironment = {
  production: false,
  msalClientId: 'YOUR_CLIENT_ID_HERE',
  msalTenantId: 'YOUR_TENANT_ID_HERE',
  redirectUri: 'http://localhost:4200',
  chatApiUrl: 'http://localhost:7071/api/nuvoco_frontend',
  chatHistoryApiUrl: 'http://localhost:7071/api',
  chatRequestTimeoutMs: 180_000,
  httpDefaultTimeoutMs: 60_000,
  adminEmails: [],
};
