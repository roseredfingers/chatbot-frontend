import {
  Component,
  EventEmitter,
  Input,
  OnDestroy,
  OnInit,
  Output,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';
import { MatDividerModule } from '@angular/material/divider';
import { Subscription } from 'rxjs';
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
export class ChatHistoryComponent implements OnInit, OnDestroy {
  @Input() activeConversationId = '';
  @Output() conversationSelected = new EventEmitter<Conversation>();
  @Output() newChat = new EventEmitter<void>();

  conversations: Conversation[] = [];
  private subscription!: Subscription;

  constructor(private chatService: ChatService) {}

  ngOnInit(): void {
    this.subscription = this.chatService
      .getConversations()
      .subscribe((convs) => {
        this.conversations = convs;
      });
  }

  ngOnDestroy(): void {
    this.subscription?.unsubscribe();
  }

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
