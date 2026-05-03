import { HttpInterceptorFn } from '@angular/common/http';

function correlationId(): string {
  try {
    return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  } catch {
    return `${Date.now()}-${Math.random()}`;
  }
}

/**
 * Adds a correlation id for tracing (Application Insights, Kusto, Function logs).
 */
export const apiInterceptor: HttpInterceptorFn = (req, next) => {
  if (req.headers.has('X-Correlation-Id')) {
    return next(req);
  }
  return next(
    req.clone({
      setHeaders: { 'X-Correlation-Id': correlationId() },
    })
  );
};
