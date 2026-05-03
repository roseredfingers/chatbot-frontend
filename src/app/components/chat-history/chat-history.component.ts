import { CommonModule } from '@angular/common';
import { Component, inject, input, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDividerModule } from '@angular/material/divider';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';
import { ChatService } from '../../services/chat.service';
import { Conversation } from '../../models/chat.model';

@Component({
  selector: 'app-chat-history',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatListModule,
    MatDividerModule,
  ],
  templateUrl: './chat-history.component.html',
  styleUrl: './chat-history.component.scss',
})
export class ChatHistoryComponent {
  readonly activeConversationId = input('');

  readonly conversationSelected = output<Conversation>();
  readonly newChat = output<void>();

  protected readonly chatService = inject(ChatService);

  selectConversation(conversation: Conversation): void {
    this.conversationSelected.emit(conversation);
  }

  startNewChat(): void {
    this.newChat.emit();
  }

  deleteConversation(event: Event, conversationId: string): void {
    event.stopPropagation();
    this.chatService.deleteConversation(conversationId);
  }
}
