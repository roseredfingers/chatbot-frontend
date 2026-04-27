import {
  Component,
  ElementRef,
  Input,
  OnChanges,
  SimpleChanges,
  ViewChild,
  AfterViewChecked,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ChatService } from '../../services/chat.service';
import { ChatMessage, Conversation } from '../../models/chat.model';

@Component({
  selector: 'app-chat-window',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatInputModule,
    MatFormFieldModule,
    MatChipsModule,
    MatProgressSpinnerModule,
  ],
  templateUrl: './chat-window.component.html',
  styleUrl: './chat-window.component.scss',
})
export class ChatWindowComponent implements OnChanges, AfterViewChecked {
  @Input() conversation!: Conversation;
  @ViewChild('messagesContainer') messagesContainer!: ElementRef;

  messages: ChatMessage[] = [];
  userInput = '';
  isLoading = false;
  private shouldScroll = false;

  constructor(private chatService: ChatService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['conversation'] && this.conversation) {
      this.messages = this.chatService.getMessages(this.conversation.id);
      this.shouldScroll = true;
    }
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll) {
      this.scrollToBottom();
      this.shouldScroll = false;
    }
  }

  sendMessage(content?: string): void {
    const message = content ?? this.userInput.trim();
    if (!message || this.isLoading) return;

    this.userInput = '';
    this.isLoading = true;
    this.shouldScroll = true;

    this.chatService
      .sendMessage(this.conversation.id, message)
      .subscribe({
        next: () => {
          this.isLoading = false;
          this.messages = this.chatService.getMessages(this.conversation.id);
          this.shouldScroll = true;
        },
        error: () => {
          this.isLoading = false;
          const errorMsg: ChatMessage = {
            role: 'bot',
            content: 'Sorry, something went wrong. Please try again.',
            timestamp: new Date(),
          };
          this.messages = [...this.messages, errorMsg];
          this.shouldScroll = true;
        },
      });
  }

  onSuggestedQuestionClick(question: string): void {
    this.sendMessage(question);
  }

  onKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  private scrollToBottom(): void {
    try {
      const el = this.messagesContainer?.nativeElement;
      if (el) {
        el.scrollTop = el.scrollHeight;
      }
    } catch (_) {}
  }
}
