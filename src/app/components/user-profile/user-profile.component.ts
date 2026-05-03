import { Component, inject, OnInit, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatToolbarModule } from '@angular/material/toolbar';
import { Router } from '@angular/router';
import { UserProfile, UserService } from '../../services/user.service';

@Component({
  selector: 'app-user-profile',
  standalone: true,
  imports: [MatCardModule, MatButtonModule, MatIconModule, MatToolbarModule],
  templateUrl: './user-profile.component.html',
  styleUrl: './user-profile.component.scss',
})
export class UserProfileComponent implements OnInit {
  private readonly userService = inject(UserService);
  private readonly router = inject(Router);

  readonly profile = signal<UserProfile | null>(null);
  readonly userInitial = signal('');

  ngOnInit(): void {
    const p = this.userService.getUserProfile();
    this.profile.set(p);
    this.userInitial.set(p.name.charAt(0).toUpperCase());
  }

  goBack(): void {
    void this.router.navigate(['/chat']);
  }
}
