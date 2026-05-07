import { CommonModule } from '@angular/common';
import {
  Component,
  computed,
  input,
} from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';
import { TokenUsageSnapshot } from '../../models/token-usage.model';

/** Circumference for r=15.9155 in a 36×36 viewBox (dasharray 100). */
const C = 100;

@Component({
  selector: 'app-token-meter',
  standalone: true,
  imports: [CommonModule, MatTooltipModule],
  templateUrl: './token-meter.component.html',
  styleUrl: './token-meter.component.scss',
})
export class TokenMeterComponent {
  readonly usage = input<TokenUsageSnapshot | null>(null);
  /** Compact inline vs larger profile variant */
  readonly size = input<'compact' | 'comfortable'>('compact');

  readonly fraction = computed(() => {
    const u = this.usage();
    if (!u || u.combined_limit <= 0) return 0;
    return Math.min(1, Math.max(0, u.combined_fraction));
  });

  readonly dashOffset = computed(() => C * (1 - this.fraction()));

  readonly strokeClass = computed(() => {
    const f = this.fraction();
    if (f < 0.7) return 'ok';
    if (f < 0.9) return 'warn';
    return 'critical';
  });

  readonly tooltip = computed(() => {
    const u = this.usage();
    if (!u) {
      return 'Usage data not loaded yet.';
    }
    const pct = (100 * this.fraction()).toFixed(1);
    return [
      `Billing period (UTC): ${u.period}`,
      `Combined: ${u.combined_used.toLocaleString()} / ${u.combined_limit.toLocaleString()} tokens (${pct}% of combined budget)`,
      `Input: ${u.input_tokens.toLocaleString()} / ${u.input_limit.toLocaleString()} (${u.input_remaining.toLocaleString()} remaining)`,
      `Output: ${u.output_tokens.toLocaleString()} / ${u.output_limit.toLocaleString()} (${u.output_remaining.toLocaleString()} remaining)`,
      'Resets at the start of each calendar month (UTC).',
    ].join('\n');
  });

  readonly atLimit = computed(() => {
    const u = this.usage();
    if (!u) return false;
    return (
      u.input_tokens >= u.input_limit || u.output_tokens >= u.output_limit
    );
  });
}
