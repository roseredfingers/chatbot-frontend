import { Component, HostListener, OnInit, ViewChild } from '@angular/core';
import { Router } from '@angular/router';
import { MatSidenav, MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { BreakpointObserver, Breakpoints } from '@angular/cdk/layout';
import { AuthService } from '../../services/auth.service';
import { ChatService } from '../../services/chat.service';
import { ChatHistoryComponent } from '../chat-history/chat-history.component';
import { ChatWindowComponent } from '../chat-window/chat-window.component';
import { Conversation } from '../../models/chat.model';

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
  @ViewChild('sidenav') sidenav!: MatSidenav;

  userName = '';
  userInitial = '';
  activeConversation: Conversation | null = null;
  isMobile = false;

  constructor(
    private authService: AuthService,
    private chatService: ChatService,
    private router: Router,
    private breakpointObserver: BreakpointObserver
  ) {}

  ngOnInit(): void {
    this.userName = this.authService.getUserName();
    this.userInitial = this.userName.charAt(0).toUpperCase();

    const userEmail = this.authService.getUserEmail();
    this.chatService.initForUser(userEmail, this.userName);

    this.breakpointObserver
      .observe([Breakpoints.Handset])
      .subscribe((result) => {
        this.isMobile = result.matches;
      });

    this.activeConversation = this.chatService.createConversation();
  }

  @HostListener('window:beforeunload')
  onBeforeUnload(): void {
    this.chatService.saveNow();
  }

  onConversationSelected(conversation: Conversation): void {
    this.activeConversation = conversation;
    // Prime the RAG backend with this conversation's history on first open.
    this.chatService.primeConversationContext(conversation);
    if (this.isMobile) {
      this.sidenav.close();
    }
  }

  onNewChat(): void {
    this.activeConversation = this.chatService.createConversation();
    if (this.isMobile) {
      this.sidenav.close();
    }
  }

  goToProfile(): void {
    this.router.navigate(['/profile']);
  }

  logout(): void {
    this.chatService.saveNow();
    this.authService.logout();
  }

  toggleSidenav(): void {
    this.sidenav.toggle();
  }
}
