export interface TokenUsageSnapshot {
  period: string;
  input_tokens: number;
  output_tokens: number;
  input_limit: number;
  output_limit: number;
  input_remaining: number;
  output_remaining: number;
  combined_used: number;
  combined_limit: number;
  /** 0–1 */
  combined_fraction: number;
}
