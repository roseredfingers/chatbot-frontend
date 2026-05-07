import { Injectable, inject } from '@angular/core';
import { MsalBroadcastService, MsalService } from '@azure/msal-angular';
import { AccountInfo, InteractionStatus } from '@azure/msal-browser';
import { filter, Observable } from 'rxjs';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly msalService = inject(MsalService);
  private readonly msalBroadcastService = inject(MsalBroadcastService);

  init(): Observable<void> {
    return new Observable<void>((observer) => {
      this.msalService.instance.initialize().then(() => {
        this.msalService.instance.handleRedirectPromise().then(() => {
          this.setActiveAccount();
          observer.next();
          observer.complete();
        });
      });
    });
  }

  login(): void {
    this.msalService.loginRedirect({
      scopes: ['user.read'],
    });
  }

  logout(): void {
    this.msalService.logoutRedirect({
      postLogoutRedirectUri: '/',
    });
  }

  isLoggedIn(): boolean {
    return this.msalService.instance.getAllAccounts().length > 0;
  }

  getUser(): AccountInfo | null {
    return this.msalService.instance.getActiveAccount();
  }

  getUserName(): string {
    return this.getUser()?.name ?? 'User';
  }

  getUserEmail(): string {
    return this.getUser()?.username ?? '';
  }

  /** Matches `environment.adminEmails` to `ADMIN_EMAILS` on the API (lowercase). */
  isAdmin(): boolean {
    const allowed = environment.adminEmails;
    if (!allowed?.length) {
      return false;
    }
    const mine = (this.getUserEmail() || '').toLowerCase().trim();
    if (!mine) {
      return false;
    }
    return allowed.some((e) => e.toLowerCase().trim() === mine);
  }

  getInteractionStatus$(): Observable<InteractionStatus> {
    return this.msalBroadcastService.inProgress$.pipe(
      filter((status) => status === InteractionStatus.None)
    );
  }

  private setActiveAccount(): void {
    const accounts = this.msalService.instance.getAllAccounts();
    if (accounts.length > 0) {
      this.msalService.instance.setActiveAccount(accounts[0]);
    }
  }
}
