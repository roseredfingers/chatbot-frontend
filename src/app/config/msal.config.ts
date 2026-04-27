import {
  MsalGuardConfiguration,
  MsalInterceptorConfiguration,
} from '@azure/msal-angular';
import {
  BrowserCacheLocation,
  Configuration,
  InteractionType,
  LogLevel,
} from '@azure/msal-browser';
import { environment } from '../../environments/environment';

export const msalConfig: Configuration = {
  auth: {
    clientId: environment.msalClientId,
    authority: `https://login.microsoftonline.com/${environment.msalTenantId}`,
    redirectUri: environment.redirectUri,
    postLogoutRedirectUri: environment.redirectUri,
  },
  cache: {
    cacheLocation: BrowserCacheLocation.SessionStorage,
  },
  system: {
    loggerOptions: {
      loggerCallback: (_level: LogLevel, message: string, containsPii: boolean) => {
        if (containsPii) return;
        console.log(message);
      },
      logLevel: LogLevel.Info,
      piiLoggingEnabled: false,
    },
  },
};

export const msalGuardConfig: MsalGuardConfiguration = {
  interactionType: InteractionType.Redirect,
  authRequest: {
    scopes: ['user.read'],
  },
};

export const msalInterceptorConfig: MsalInterceptorConfiguration = {
  interactionType: InteractionType.Redirect,
  protectedResourceMap: new Map<string, string[]>([
    ['https://graph.microsoft.com/v1.0/*', ['user.read']],
  ]),
};
