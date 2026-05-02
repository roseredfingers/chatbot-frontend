export interface ChatResponse {
  answer: string;
  suggested_questions: string[];
  status: number;
}

export interface Conversation {
  id: string;
  title: string;
  lastUpdated: Date;
  messages: ChatMessage[];
}

export interface ChatMessage {
  role: 'user' | 'bot';
  content: string;
  suggestedQuestions?: string[];
  timestamp: Date;
}

export interface StoredConversation {
  id: string;
  title: string;
  lastUpdated: string;
  messages: StoredMessage[];
}

export interface StoredMessage {
  role: 'user' | 'bot';
  content: string;
  suggestedQuestions?: string[];
  timestamp: string;
}

export interface AppendExchangeRequest {
  user_id: string;
  conversation_id: string;
  conversation_title: string;
  user_message: { content: string; timestamp: string };
  assistant_message: { content: string; timestamp: string; suggestedQuestions?: string[] };
}

export interface PrimeConversationRequest {
  session_id: string;
  messages: StoredMessage[];
}
