export interface ChatRequest {
  message: string;
  session_id: string;
}

export interface ChatResponse {
  answer: string;
  suggested_questions: string[];
  status: string;
}

export interface Conversation {
  id: string;
  title: string;
  lastUpdated: Date;
}

export interface ChatMessage {
  role: 'user' | 'bot';
  content: string;
  suggestedQuestions?: string[];
  timestamp: Date;
}
