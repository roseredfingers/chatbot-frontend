import { Injectable, inject } from '@angular/core';
import { AuthService } from './auth.service';

export interface UserProfile {
  name: string;
  email: string;
  phone: string;
}

@Injectable({ providedIn: 'root' })
export class UserService {
  private readonly authService = inject(AuthService);

  getUserProfile(): UserProfile {
    const user = this.authService.getUser();
    return {
      name: user?.name ?? 'Unknown User',
      email: user?.username ?? 'unknown@example.com',
      phone: '+1 (555) 000-0000',
    };
  }
}
