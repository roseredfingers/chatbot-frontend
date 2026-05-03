import { CommonModule } from '@angular/common';
import {
  Component,
  computed,
  effect,
  ElementRef,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ChatMessage, Conversation } from '../../models/chat.model';
import { MarkdownPipe } from '../../pipes/markdown.pipe';
import { ChatService } from '../../services/chat.service';

@Component({
  selector: 'app-chat-window',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MarkdownPipe,
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
export class ChatWindowComponent {
  private readonly chatService = inject(ChatService);

  readonly conversation = input.required<Conversation>();
  private readonly messagesContainer =
    viewChild<ElementRef<HTMLElement>>('messagesContainer');

  readonly userInput = signal('');
  readonly isLoading = signal(false);

  readonly messages = computed<ChatMessage[]>(() => {
    const id = this.conversation().id;
    const list = this.chatService.conversations();
    return list.find((c) => c.id === id)?.messages ?? [];
  });

  constructor() {
    effect(() => {
      void this.messages().length;
      void this.isLoading();
      queueMicrotask(() => this.scrollToBottom());
    });
  }

  sendInternal(message: string): void {
    if (!message || this.isLoading()) return;

    this.userInput.set('');
    this.isLoading.set(true);

    this.chatService.sendMessage(this.conversation().id, message).subscribe({
      next: () => this.isLoading.set(false),
      error: () => this.isLoading.set(false),
    });
  }

  sendMessage(content?: string): void {
    const message = (content ?? this.userInput()).trim();
    this.sendInternal(message);
  }

  onSuggestedQuestionClick(question: string): void {
    this.sendInternal(question.trim());
  }

  onKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  private scrollToBottom(): void {
    const el = this.messagesContainer()?.nativeElement;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }
}
