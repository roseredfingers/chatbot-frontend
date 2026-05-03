import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of, map, catchError, timeout } from 'rxjs';
import {
  AppendExchangeRequest,
  Conversation,
  PrimeConversationRequest,
  StoredConversation,
} from '../models/chat.model';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ChatHistoryStorageService {
  private readonly http = inject(HttpClient);

  loadHistory(userId: string): Observable<Conversation[]> {
    const url = `${environment.chatHistoryApiUrl}/chat_history?user_id=${encodeURIComponent(userId)}`;

    return this.http.get<StoredConversation[]>(url).pipe(
      timeout({ first: environment.httpDefaultTimeoutMs }),
      map((stored) => this.deserializeConversations(stored)),
      catchError(() => of([]))
    );
  }

  primeConversationHistory(
    req: PrimeConversationRequest
  ): Observable<void> {
    const url = `${environment.chatHistoryApiUrl}/prime_conversation`;
    return this.http.post(url, req).pipe(
      timeout({ first: environment.httpDefaultTimeoutMs }),
      map(() => void 0),
      catchError((err) => {
        console.error('Failed to prime conversation history:', err);
        return of(void 0);
      })
    );
  }

  appendExchange(req: AppendExchangeRequest): Observable<void> {
    const url = `${environment.chatHistoryApiUrl}/append_exchange`;
    return this.http.post(url, req).pipe(
      timeout({ first: environment.httpDefaultTimeoutMs }),
      map(() => void 0),
      catchError((err) => {
        console.error('Failed to append exchange:', err);
        return of(void 0);
      })
    );
  }

  saveHistory(userId: string, conversations: Conversation[]): Observable<void> {
    const url = `${environment.chatHistoryApiUrl}/chat_history`;

    const payload = {
      user_id: userId,
      conversations: this.serializeConversations(conversations),
    };

    return this.http.post(url, payload).pipe(
      timeout({ first: environment.httpDefaultTimeoutMs }),
      map(() => void 0),
      catchError((err) => {
        console.error('Failed to save chat history:', err);
        return of(void 0);
      })
    );
  }

  deleteHistory(userId: string, conversationId: string): Observable<void> {
    const url = `${environment.chatHistoryApiUrl}/chat_history_delete`;

    return this.http
      .post(url, { user_id: userId, conversation_id: conversationId })
      .pipe(
        timeout({ first: environment.httpDefaultTimeoutMs }),
        map(() => void 0),
        catchError((err) => {
          console.error('Failed to delete chat history:', err);
          return of(void 0);
        })
      );
  }

  serializeConversation(conv: Conversation): StoredConversation {
    return {
      id: conv.id,
      title: conv.title,
      lastUpdated: conv.lastUpdated.toISOString(),
      messages: conv.messages.map((msg) => ({
        role: msg.role,
        content: msg.content,
        suggestedQuestions: msg.suggestedQuestions,
        timestamp: msg.timestamp.toISOString(),
      })),
    };
  }

  private serializeConversations(conversations: Conversation[]): StoredConversation[] {
    return conversations.map((c) => this.serializeConversation(c));
  }

  private deserializeConversations(stored: StoredConversation[]): Conversation[] {
    return (stored || []).map((conv) => ({
      id: conv.id,
      title: conv.title,
      lastUpdated: new Date(conv.lastUpdated),
      messages: (conv.messages || []).map((msg) => ({
        role: msg.role,
        content: msg.content,
        suggestedQuestions: msg.suggestedQuestions,
        timestamp: new Date(msg.timestamp),
      })),
    }));
  }
}
