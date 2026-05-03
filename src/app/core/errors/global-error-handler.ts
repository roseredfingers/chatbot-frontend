import { ErrorHandler, Injectable, isDevMode } from '@angular/core';
import { environment } from '../../../environments/environment';

/**
 * Central sink for unhandled errors (avoids silent failures in production builds).
 */
@Injectable()
export class GlobalErrorHandler implements ErrorHandler {
  handleError(error: unknown): void {
    const payload =
      error instanceof Error
        ? { message: error.message, stack: error.stack, name: error.name }
        : { message: String(error) };

    if (!environment.production || isDevMode()) {
      console.error('[GlobalErrorHandler]', payload, error);
      return;
    }

    console.error('[GlobalErrorHandler]', payload.message);

    // Hook: send to Application Insights / Sentry, etc.
  }
}
