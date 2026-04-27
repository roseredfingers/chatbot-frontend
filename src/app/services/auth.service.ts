import { Injectable } from '@angular/core';
import { MsalBroadcastService, MsalService } from '@azure/msal-angular';
import { AccountInfo, InteractionStatus } from '@azure/msal-browser';
import { filter, Observable, Subject } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly _destroy$ = new Subject<void>();

  constructor(
    private msalService: MsalService,
    private msalBroadcastService: MsalBroadcastService
  ) {}

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

  destroy(): void {
    this._destroy$.next();
    this._destroy$.complete();
  }
}
