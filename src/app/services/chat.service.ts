import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, map, catchError, of } from 'rxjs';
import { v4 as uuidv4 } from 'uuid';
import {
  ChatMessage,
  ChatResponse,
  Conversation,
} from '../models/chat.model';
import { ChatHistoryStorageService } from './chat-history-storage.service';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ChatService {
  private conversations$ = new BehaviorSubject<Conversation[]>([]);
  private currentUserId = '';
  private currentUserName = '';
  private saveDebounceTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private http: HttpClient,
    private storageService: ChatHistoryStorageService
  ) {}

  initForUser(userId: string, userName: string): void {
    this.currentUserId = userId;
    this.currentUserName = userName;
    this.storageService.loadHistory(userId).subscribe((conversations) => {
      if (conversations.length > 0) {
        this.conversations$.next(conversations);
      }
    });
  }

  getConversations(): Observable<Conversation[]> {
    return this.conversations$.asObservable();
  }

  getMessages(conversationId: string): ChatMessage[] {
    const conv = this.conversations$.value.find((c) => c.id === conversationId);
    return conv?.messages ?? [];
  }

  sendMessage(
    sessionId: string,
    message: string
  ): Observable<ChatResponse> {
    const userMessage: ChatMessage = {
      role: 'user',
      content: message,
      timestamp: new Date(),
    };

    this.addMessageToConversation(sessionId, userMessage);
    this.updateConversationTitle(sessionId, message);

    const payload = {
      type: 'message',
      text: message,
      channelId: sessionId,
      from: { id: this.currentUserId, name: this.currentUserName },
      conversation: { id: sessionId },
      timestamp: new Date().toISOString(),
    };

    return this.http
      .post<ChatResponse>(environment.chatApiUrl, payload)
      .pipe(
        map((response) => {
          const answer = response.answer ?? '';
          const suggestedQuestions = response.suggested_questions ?? [];

          const botMessage: ChatMessage = {
            role: 'bot',
            content: answer,
            suggestedQuestions,
            timestamp: new Date(),
          };
          this.addMessageToConversation(sessionId, botMessage);
          this.debouncedSave();

          return response;
        }),
        catchError((err) => {
          console.error('Chat API error:', err);
          const errorMsg: ChatMessage = {
            role: 'bot',
            content: 'Sorry, something went wrong. Please try again.',
            timestamp: new Date(),
          };
          this.addMessageToConversation(sessionId, errorMsg);
          return of({
            answer: errorMsg.content,
            suggested_questions: [],
            status: 500,
          });
        })
      );
  }

  createConversation(): Conversation {
    const conversation: Conversation = {
      id: uuidv4(),
      title: 'New Chat',
      lastUpdated: new Date(),
      messages: [],
    };
    const current = this.conversations$.value;
    this.conversations$.next([conversation, ...current]);
    return conversation;
  }

  deleteConversation(conversationId: string): void {
    const current = this.conversations$.value.filter(
      (c) => c.id !== conversationId
    );
    this.conversations$.next(current);
    this.storageService
      .deleteHistory(this.currentUserId, conversationId)
      .subscribe();
  }

  saveNow(): void {
    if (!this.currentUserId) return;
    this.storageService
      .saveHistory(this.currentUserId, this.conversations$.value)
      .subscribe();
  }

  private addMessageToConversation(
    conversationId: string,
    message: ChatMessage
  ): void {
    const conversations = this.conversations$.value;
    const conv = conversations.find((c) => c.id === conversationId);
    if (conv) {
      conv.messages.push(message);
      conv.lastUpdated = new Date();
      this.conversations$.next([...conversations]);
    }
  }

  private updateConversationTitle(
    conversationId: string,
    firstMessage: string
  ): void {
    const conversations = this.conversations$.value;
    const conv = conversations.find((c) => c.id === conversationId);
    if (conv && conv.title === 'New Chat') {
      conv.title =
        firstMessage.length > 40
          ? firstMessage.substring(0, 40) + '...'
          : firstMessage;
      this.conversations$.next([...conversations]);
    }
  }

  private debouncedSave(): void {
    if (this.saveDebounceTimer) {
      clearTimeout(this.saveDebounceTimer);
    }
    this.saveDebounceTimer = setTimeout(() => {
      this.saveNow();
    }, 3000);
  }
}
