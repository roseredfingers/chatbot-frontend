import type { AppEnvironment } from './environment.interface';

/** Production build — set real values before deploying artifacts */
export const environment: AppEnvironment = {
  production: true,
  msalClientId: 'YOUR_CLIENT_ID_HERE',
  msalTenantId: 'YOUR_TENANT_ID_HERE',
  redirectUri: 'https://YOUR_STATIC_WEB_APP.azurestaticapps.net',
  chatApiUrl: 'https://YOUR_FUNCTION_APP.azurewebsites.net/api/nuvoco_frontend',
  chatHistoryApiUrl: 'https://YOUR_FUNCTION_APP.azurewebsites.net/api',
  chatRequestTimeoutMs: 180_000,
  httpDefaultTimeoutMs: 60_000,
};
