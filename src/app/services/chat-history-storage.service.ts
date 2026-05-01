import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of, map, catchError } from 'rxjs';
import {
  Conversation,
  StoredConversation,
} from '../models/chat.model';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ChatHistoryStorageService {
  constructor(private http: HttpClient) {}

  loadHistory(userId: string): Observable<Conversation[]> {
    const url = `${environment.chatHistoryApiUrl}/chat_history?user_id=${encodeURIComponent(userId)}`;

    return this.http.get<StoredConversation[]>(url).pipe(
      map((stored) => this.deserializeConversations(stored)),
      catchError(() => of([]))
    );
  }

  saveHistory(userId: string, conversations: Conversation[]): Observable<void> {
    const url = `${environment.chatHistoryApiUrl}/chat_history`;

    const payload = {
      user_id: userId,
      conversations: this.serializeConversations(conversations),
    };

    return this.http.post(url, payload).pipe(
      map(() => void 0),
      catchError((err) => {
        console.error('Failed to save chat history:', err);
        return of(void 0);
      })
    );
  }

  deleteHistory(userId: string, conversationId: string): Observable<void> {
    const url = `${environment.chatHistoryApiUrl}/chat_history/delete`;

    return this.http
      .post(url, { user_id: userId, conversation_id: conversationId })
      .pipe(
        map(() => void 0),
        catchError((err) => {
          console.error('Failed to delete conversation:', err);
          return of(void 0);
        })
      );
  }

  private serializeConversations(conversations: Conversation[]): StoredConversation[] {
    return conversations.map((conv) => ({
      id: conv.id,
      title: conv.title,
      lastUpdated: conv.lastUpdated.toISOString(),
      messages: conv.messages.map((msg) => ({
        role: msg.role,
        content: msg.content,
        suggestedQuestions: msg.suggestedQuestions,
        timestamp: msg.timestamp.toISOString(),
      })),
    }));
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
