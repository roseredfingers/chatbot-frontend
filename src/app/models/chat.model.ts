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
