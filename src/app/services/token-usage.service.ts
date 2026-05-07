import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { catchError, of, tap, timeout } from 'rxjs';
import { environment } from '../../environments/environment';
import { TokenUsageSnapshot } from '../models/token-usage.model';

@Injectable({ providedIn: 'root' })
export class TokenUsageService {
  private readonly http = inject(HttpClient);

  /** Latest usage from GET /token_usage or chat response (null if unknown). */
  readonly snapshot = signal<TokenUsageSnapshot | null>(null);

  /** Refetch from blob-backed API (e.g. on chat screen open). */
  load(userId: string): void {
    if (!userId.trim()) {
      this.snapshot.set(null);
      return;
    }
    const url = `${environment.chatHistoryApiUrl}/token_usage?user_id=${encodeURIComponent(userId)}`;
    this.http
      .get<TokenUsageSnapshot>(url)
      .pipe(
        timeout({ first: environment.httpDefaultTimeoutMs }),
        tap((d) => this.snapshot.set(d)),
        catchError(() => {
          this.snapshot.set(null);
          return of(null);
        })
      )
      .subscribe();
  }

  applyFromResponse(data: TokenUsageSnapshot | undefined): void {
    if (data) {
      this.snapshot.set(data);
    }
  }

  clear(): void {
    this.snapshot.set(null);
  }
}
