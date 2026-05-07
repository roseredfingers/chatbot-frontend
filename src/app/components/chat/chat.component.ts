import { BreakpointObserver, Breakpoints } from '@angular/cdk/layout';
import { Component, HostListener, inject, OnInit, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { ChatService } from '../../services/chat.service';
import { TokenUsageService } from '../../services/token-usage.service';
import { Conversation } from '../../models/chat.model';
import { ChatHistoryComponent } from '../chat-history/chat-history.component';
import { ChatWindowComponent } from '../chat-window/chat-window.component';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [
    MatSidenavModule,
    MatToolbarModule,
    MatButtonModule,
    MatIconModule,
    MatMenuModule,
    ChatHistoryComponent,
    ChatWindowComponent,
  ],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.scss',
})
export class ChatComponent implements OnInit {
  protected readonly authService = inject(AuthService);
  private readonly chatService = inject(ChatService);
  private readonly tokenUsage = inject(TokenUsageService);
  private readonly router = inject(Router);
  private readonly breakpointObserver = inject(BreakpointObserver);

  readonly userInitial = signal('');
  readonly activeConversation = signal<Conversation | null>(null);
  readonly isMobile = signal(false);
  /** Desktop: drawer pushes chat; closed = full width. Mobile: overlay; usually starts closed. */
  readonly sidenavOpened = signal(true);

  ngOnInit(): void {
    const name = this.authService.getUserName();
    this.userInitial.set(name.charAt(0).toUpperCase());

    const userEmail = this.authService.getUserEmail();
    this.chatService.initForUser(userEmail, name);

    this.breakpointObserver.observe([Breakpoints.Handset]).subscribe((result) => {
      const mobile = result.matches;
      this.isMobile.set(mobile);
      if (mobile) {
        this.sidenavOpened.set(false);
      } else {
        this.sidenavOpened.set(true);
      }
    });

    this.activeConversation.set(this.chatService.createConversation());
  }

  @HostListener('window:beforeunload')
  onBeforeUnload(): void {
    this.chatService.saveNow();
  }

  onConversationSelected(conversation: Conversation): void {
    this.activeConversation.set(conversation);
    this.chatService.primeConversationContext(conversation);
    if (this.isMobile()) {
      this.sidenavOpened.set(false);
    }
  }

  onNewChat(): void {
    this.activeConversation.set(this.chatService.createConversation());
    if (this.isMobile()) {
      this.sidenavOpened.set(false);
    }
  }

  goToProfile(): void {
    this.router.navigate(['/profile']);
  }

  goToAdmin(): void {
    void this.router.navigate(['/admin']);
  }

  logout(): void {
    this.chatService.saveNow();
    this.tokenUsage.clear();
    this.authService.logout();
  }

  toggleSidenav(): void {
    this.sidenavOpened.update((v) => !v);
  }

  onSidenavOpenedChange(opened: boolean): void {
    this.sidenavOpened.set(opened);
  }
}
