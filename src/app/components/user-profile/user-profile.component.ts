import { CommonModule } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatToolbarModule } from '@angular/material/toolbar';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { UserProfile, UserService } from '../../services/user.service';
import { TokenUsageService } from '../../services/token-usage.service';
import { TokenMeterComponent } from '../token-meter/token-meter.component';

@Component({
  selector: 'app-user-profile',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatButtonModule, MatIconModule, MatToolbarModule, TokenMeterComponent],
  templateUrl: './user-profile.component.html',
  styleUrl: './user-profile.component.scss',
})
export class UserProfileComponent implements OnInit {
  private readonly userService = inject(UserService);
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  readonly tokenUsage = inject(TokenUsageService);

  readonly profile = signal<UserProfile | null>(null);
  readonly userInitial = signal('');

  ngOnInit(): void {
    const p = this.userService.getUserProfile();
    this.profile.set(p);
    this.userInitial.set(p.name.charAt(0).toUpperCase());
    this.tokenUsage.load(this.authService.getUserEmail());
  }

  goBack(): void {
    void this.router.navigate(['/chat']);
  }
}
