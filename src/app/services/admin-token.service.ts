import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  AdminTokenOverview,
  AdminTokenUsersResponse,
} from '../models/admin-token.model';
import { AuthService } from './auth.service';

@Injectable({ providedIn: 'root' })
export class AdminTokenService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);

  private headers(): HttpHeaders {
    let h = new HttpHeaders();
    const email = this.auth.getUserEmail();
    if (email) {
      h = h.set('X-Admin-User-Email', email);
    }
    const key = environment.adminApiKey;
    if (key) {
      h = h.set('X-Admin-Api-Key', key);
    }
    return h;
  }

  getOverview(year: number): Observable<AdminTokenOverview> {
    const url = `${environment.chatHistoryApiUrl}/admin/token_overview`;
    return this.http.get<AdminTokenOverview>(url, {
      params: { year: String(year) },
      headers: this.headers(),
    });
  }

  getUsers(): Observable<AdminTokenUsersResponse> {
    const url = `${environment.chatHistoryApiUrl}/admin/token_users`;
    return this.http.get<AdminTokenUsersResponse>(url, {
      headers: this.headers(),
    });
  }

  setLimits(
    emails: string[],
    inputLimit: number,
    outputLimit: number
  ): Observable<{ updated: string[]; count: number }> {
    const url = `${environment.chatHistoryApiUrl}/admin/token_limits`;
    return this.http.post<{ updated: string[]; count: number }>(
      url,
      { emails, input_limit: inputLimit, output_limit: outputLimit },
      { headers: this.headers() }
    );
  }
}
