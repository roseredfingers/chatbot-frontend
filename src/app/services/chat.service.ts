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

  /**
   * Tracks which conversation IDs have already been primed into the RAG
   * backend this login session. Cleared on logout / page reload.
   */
  private primedConversations = new Set<string>();

  constructor(
    private http: HttpClient,
    private storageService: ChatHistoryStorageService
  ) {}

  // ─── Initialization ───────────────────────────────────────────────────────

  /**
   * Called once after login. Loads all conversations from Azure Blob Storage
   * and populates the sidebar.
   */
  initForUser(userId: string, userName: string): void {
    this.currentUserId = userId;
    this.currentUserName = userName;
    this.primedConversations.clear();

    this.storageService.loadHistory(userId).subscribe((conversations) => {
      if (conversations.length > 0) {
        this.conversations$.next(conversations);
      }
    });
  }

  // ─── Queries ──────────────────────────────────────────────────────────────

  getConversations(): Observable<Conversation[]> {
    return this.conversations$.asObservable();
  }

  getMessages(conversationId: string): ChatMessage[] {
    const conv = this.conversations$.value.find((c) => c.id === conversationId);
    return conv?.messages ?? [];
  }

  // ─── Open a conversation (prime RAG on first open) ─────────────────────────

  /**
   * Must be called whenever the user opens a conversation from the sidebar.
   * On the first open within this login session the full message history is
   * sent to the RAG backend so the LangGraph thread has context. Subsequent
   * opens are no-ops (tracked by `primedConversations`).
   */
  primeConversationContext(conversation: Conversation): void {
    if (this.primedConversations.has(conversation.id)) {
      return;
    }
    if (!conversation.messages.length) {
      // Nothing to prime for a brand-new conversation.
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

  // ─── Messaging ────────────────────────────────────────────────────────────

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
        map((response) => {
          const answer = response.answer ?? '';
          const suggestedQuestions = response.suggested_questions ?? [];
          const botTimestamp = new Date();

          const botMessage: ChatMessage = {
            role: 'bot',
            content: answer,
            suggestedQuestions,
            timestamp: botTimestamp,
          };
          this.addMessageToConversation(sessionId, botMessage);

          // Persist the exchange immediately to Azure Blob Storage.
          this.appendExchange(
            sessionId,
            userMessage,
            botMessage,
          );

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

  // ─── Conversation management ──────────────────────────────────────────────

  createConversation(): Conversation {
    const conversation: Conversation = {
      id: uuidv4(),
      title: 'New Chat',
      lastUpdated: new Date(),
      messages: [],
    };
    const current = this.conversations$.value;
    this.conversations$.next([conversation, ...current]);
    // Mark as primed immediately (no history to inject).
    this.primedConversations.add(conversation.id);
    return conversation;
  }

  deleteConversation(conversationId: string): void {
    const current = this.conversations$.value.filter(
      (c) => c.id !== conversationId
    );
    this.conversations$.next(current);
    this.primedConversations.delete(conversationId);
    this.storageService
      .deleteHistory(this.currentUserId, conversationId)
      .subscribe();
  }

  /** Bulk-save all conversations (used on logout / beforeunload). */
  saveNow(): void {
    if (!this.currentUserId) return;
    this.storageService
      .saveHistory(this.currentUserId, this.conversations$.value)
      .subscribe();
  }

  // ─── Private helpers ──────────────────────────────────────────────────────

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

  /**
   * Immediately persists one user + assistant exchange to Azure Blob Storage.
   * Called right after every assistant response.
   */
  private appendExchange(
    conversationId: string,
    userMessage: ChatMessage,
    botMessage: ChatMessage,
  ): void {
    if (!this.currentUserId) return;

    const conv = this.conversations$.value.find((c) => c.id === conversationId);
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
