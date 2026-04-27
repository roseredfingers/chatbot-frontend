import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of, delay, tap } from 'rxjs';
import { v4 as uuidv4 } from 'uuid';
import {
  ChatMessage,
  ChatRequest,
  ChatResponse,
  Conversation,
} from '../models/chat.model';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ChatService {
  private conversations$ = new BehaviorSubject<Conversation[]>([]);
  private messagesMap = new Map<string, ChatMessage[]>();

  constructor(private http: HttpClient) {
    this.loadMockConversations();
  }

  getConversations(): Observable<Conversation[]> {
    return this.conversations$.asObservable();
  }

  getMessages(conversationId: string): ChatMessage[] {
    return this.messagesMap.get(conversationId) ?? [];
  }

  sendMessage(sessionId: string, message: string): Observable<ChatResponse> {
    const userMessage: ChatMessage = {
      role: 'user',
      content: message,
      timestamp: new Date(),
    };

    const existing = this.messagesMap.get(sessionId) ?? [];
    existing.push(userMessage);
    this.messagesMap.set(sessionId, existing);
    this.updateConversationTitle(sessionId, message);

    const payload: ChatRequest = {
      message,
      session_id: sessionId,
    };

    return this.http
      .post<ChatResponse>(environment.chatApiUrl, payload)
      .pipe(
        tap((response) => {
          const botMessage: ChatMessage = {
            role: 'bot',
            content: response.answer,
            suggestedQuestions: response.suggested_questions,
            timestamp: new Date(),
          };
          existing.push(botMessage);
        })
      );
  }

  createConversation(): Conversation {
    const conversation: Conversation = {
      id: uuidv4(),
      title: 'New Chat',
      lastUpdated: new Date(),
    };
    const current = this.conversations$.value;
    this.conversations$.next([conversation, ...current]);
    this.messagesMap.set(conversation.id, []);
    return conversation;
  }

  deleteConversation(conversationId: string): void {
    const current = this.conversations$.value.filter(
      (c) => c.id !== conversationId
    );
    this.conversations$.next(current);
    this.messagesMap.delete(conversationId);
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
    }
    conv!.lastUpdated = new Date();
    this.conversations$.next([...conversations]);
  }

  // TODO: Replace with Cosmos DB queries
  private loadMockConversations(): void {
    const mockConversations: Conversation[] = [
      {
        id: uuidv4(),
        title: 'How to deploy to Azure?',
        lastUpdated: new Date(Date.now() - 86400000),
      },
      {
        id: uuidv4(),
        title: 'Explain microservices architecture',
        lastUpdated: new Date(Date.now() - 172800000),
      },
    ];

    mockConversations.forEach((conv) => {
      this.messagesMap.set(conv.id, [
        {
          role: 'user',
          content: conv.title,
          timestamp: new Date(conv.lastUpdated.getTime() - 60000),
        },
        {
          role: 'bot',
          content: `This is a mock response for "${conv.title}". Connect to Cosmos DB to load real history.`,
          suggestedQuestions: [
            'Tell me more',
            'What are the best practices?',
            'Can you give an example?',
          ],
          timestamp: conv.lastUpdated,
        },
      ]);
    });

    this.conversations$.next(mockConversations);
  }
}
