export interface DailyAggRow {
  input_tokens: number;
  output_tokens: number;
  users: number;
}

export interface MonthlyAggRow {
  input_tokens: number;
  output_tokens: number;
  users: number;
}

export interface AdminTokenOverview {
  year: number;
  generated_at_utc: string;
  users_with_usage_blobs: number;
  year_totals: {
    input_tokens: number;
    output_tokens: number;
    combined: number;
  };
  current_utc_month: string;
  current_month_totals: {
    input_tokens: number;
    output_tokens: number;
    combined: number;
    users: number;
  };
  today_utc: string;
  today_totals: {
    input_tokens: number;
    output_tokens: number;
    combined: number;
    users: number;
  };
  daily: Record<string, DailyAggRow>;
  monthly: Record<string, MonthlyAggRow>;
}

export interface AdminTokenUserRow {
  user_id: string;
  input_limit: number;
  output_limit: number;
  default_input_limit: number;
  default_output_limit: number;
  has_custom_limits: boolean;
}

export interface AdminTokenUsersResponse {
  users: AdminTokenUserRow[];
}

export type HeatCell = {
  dateStr: string;
  inYear: boolean;
  /** input + output tokens (estimated) */
  total: number;
  level: number;
};
