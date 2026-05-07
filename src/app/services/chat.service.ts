import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { toObservable } from '@angular/core/rxjs-interop';
import {
  Observable,
  TimeoutError,
  catchError,
  map,
  of,
  timeout,
} from 'rxjs';
import { v4 as uuidv4 } from 'uuid';
import {
  ChatMessage,
  ChatResponse,
  Conversation,
} from '../models/chat.model';
import { ChatHistoryStorageService } from './chat-history-storage.service';
import { TokenUsageService } from './token-usage.service';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly http = inject(HttpClient);
  private readonly storageService = inject(ChatHistoryStorageService);
  private readonly tokenUsage = inject(TokenUsageService);

  private readonly _conversations = signal<Conversation[]>([]);
  /** Sidebar and message views should read this signal (or `getConversations()`). */
  readonly conversations = this._conversations.asReadonly();

  private readonly conversations$ = toObservable(this._conversations);

  private currentUserId = '';
  private currentUserName = '';

  /**
   * Tracks which conversation IDs have already been primed into the RAG
   * backend this login session. Cleared on logout / page reload.
   */
  private primedConversations = new Set<string>();

  /** Kept for callers that still prefer Observables over signals. */
  getConversations(): Observable<Conversation[]> {
    return this.conversations$;
  }

  initForUser(userId: string, userName: string): void {
    this.currentUserId = userId;
    this.currentUserName = userName;
    this.primedConversations.clear();
    this.tokenUsage.load(userId);

    this.storageService.loadHistory(userId).subscribe((conversations) => {
      if (conversations.length > 0) {
        const existing = this._conversations();
        const loadedIds = new Set(conversations.map((c) => c.id));
        const kept = existing.filter((c) => !loadedIds.has(c.id));
        this._conversations.set([...kept, ...conversations]);
      }
    });
  }

  getMessages(conversationId: string): ChatMessage[] {
    const conv = this._conversations().find((c) => c.id === conversationId);
    return conv?.messages ?? [];
  }

  primeConversationContext(conversation: Conversation): void {
    if (this.primedConversations.has(conversation.id)) {
      return;
    }
    if (!conversation.messages.length) {
      this.primedConversations.add(conversation.id);
      return;
    }

    this.primedConversations.add(conversation.id);

    const storedMessages = conversation.messages.map((msg) => ({
      role: msg.role,
      content: msg.content,
      suggestedQuestions: msg.suggestedQuestions,
      timestamp: msg.timestamp.toISOString(),
    }));

    this.storageService
      .primeConversationHistory({
        session_id: conversation.id,
        messages: storedMessages,
      })
      .subscribe();
  }

  sendMessage(sessionId: string, message: string): Observable<ChatResponse> {
    const userTimestamp = new Date();
    const userMessage: ChatMessage = {
      role: 'user',
      content: message,
      timestamp: userTimestamp,
    };

    this.addMessageToConversation(sessionId, userMessage);
    this.updateConversationTitle(sessionId, message);

    const payload = {
      type: 'message',
      text: message,
      channelId: sessionId,
      from: { id: this.currentUserId, name: this.currentUserName },
      conversation: { id: sessionId },
      timestamp: userTimestamp.toISOString(),
    };

    return this.http
      .post<ChatResponse>(environment.chatApiUrl, payload)
      .pipe(
        timeout({ first: environment.chatRequestTimeoutMs }),
        map((response) => {
          const answer = response.answer ?? '';
          const suggestedQuestions = response.suggested_questions ?? [];
          const botTimestamp = new Date();

          this.tokenUsage.applyFromResponse(response.token_usage);

          const botMessage: ChatMessage = {
            role: 'bot',
            content: answer,
            suggestedQuestions,
            timestamp: botTimestamp,
          };
          this.addMessageToConversation(sessionId, botMessage);

          this.appendExchange(sessionId, userMessage, botMessage);

          return response;
        }),
        catchError((err) => {
          if (err instanceof HttpErrorResponse && err.status === 429) {
            const body = err.error as { error?: string } | null;
            const lim =
              body && typeof body.error === 'string'
                ? body.error
                : 'Your monthly token budget is used up. It resets next month (UTC).';
            console.warn('Token limit:', lim);
            this.tokenUsage.load(this.currentUserId);
            const errorMsg: ChatMessage = {
              role: 'bot',
              content: lim,
              timestamp: new Date(),
            };
            this.addMessageToConversation(sessionId, errorMsg);
            return of({
              answer: errorMsg.content,
              suggested_questions: [],
              status: 429,
            });
          }
          if (err instanceof TimeoutError) {
            console.error('Chat API timed out after', environment.chatRequestTimeoutMs, 'ms');
          } else {
            console.error('Chat API error:', err);
          }
          const errorMsg: ChatMessage = {
            role: 'bot',
            content:
              err instanceof TimeoutError
                ? 'The assistant took too long to respond. Please try again.'
                : 'Sorry, something went wrong. Please try again.',
            timestamp: new Date(),
          };
          this.addMessageToConversation(sessionId, errorMsg);
          return of({
            answer: errorMsg.content,
            suggested_questions: [],
            status: err instanceof TimeoutError ? 504 : 500,
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
    const current = this._conversations();
    this._conversations.set([conversation, ...current]);
    this.primedConversations.add(conversation.id);
    return conversation;
  }

  deleteConversation(conversationId: string): void {
    const current = this._conversations().filter(
      (c) => c.id !== conversationId
    );
    this._conversations.set(current);
    this.primedConversations.delete(conversationId);
    this.storageService
      .deleteHistory(this.currentUserId, conversationId)
      .subscribe();
  }

  saveNow(): void {
    if (!this.currentUserId) return;
    this.storageService
      .saveHistory(this.currentUserId, this._conversations())
      .subscribe();
  }

  private addMessageToConversation(
    conversationId: string,
    message: ChatMessage
  ): void {
    this._conversations.update((conversations) =>
      conversations.map((c) =>
        c.id === conversationId
          ? { ...c, messages: [...c.messages, message], lastUpdated: new Date() }
          : c
      )
    );
  }

  private updateConversationTitle(
    conversationId: string,
    firstMessage: string
  ): void {
    this._conversations.update((conversations) =>
      conversations.map((c) =>
        c.id === conversationId && c.title === 'New Chat'
          ? {
              ...c,
              title:
                firstMessage.length > 40
                  ? firstMessage.substring(0, 40) + '...'
                  : firstMessage,
            }
          : c
      )
    );
  }

  private appendExchange(
    conversationId: string,
    userMessage: ChatMessage,
    botMessage: ChatMessage
  ): void {
    if (!this.currentUserId) return;

    const conv = this._conversations().find((c) => c.id === conversationId);
    const title = conv?.title ?? 'New Chat';

    this.storageService
      .appendExchange({
        user_id: this.currentUserId,
        conversation_id: conversationId,
        conversation_title: title,
        user_message: {
          content: userMessage.content,
          timestamp: userMessage.timestamp.toISOString(),
        },
        assistant_message: {
          content: botMessage.content,
          timestamp: botMessage.timestamp.toISOString(),
          suggestedQuestions: botMessage.suggestedQuestions,
        },
      })
      .subscribe();
  }
}
