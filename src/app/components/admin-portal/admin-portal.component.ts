import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Router } from '@angular/router';
import { finalize } from 'rxjs';
import {
  AdminTokenOverview,
  AdminTokenUserRow,
  DailyAggRow,
  HeatCell,
} from '../../models/admin-token.model';
import { AdminTokenService } from '../../services/admin-token.service';

@Component({
  selector: 'app-admin-portal',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatToolbarModule,
    MatButtonModule,
    MatIconModule,
    MatCardModule,
    MatFormFieldModule,
    MatSelectModule,
    MatInputModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    MatTooltipModule,
  ],
  templateUrl: './admin-portal.component.html',
  styleUrl: './admin-portal.component.scss',
})
export class AdminPortalComponent implements OnInit {
  private readonly adminApi = inject(AdminTokenService);
  private readonly router = inject(Router);
  private readonly snack = inject(MatSnackBar);

  readonly loadError = signal<string | null>(null);
  readonly overview = signal<AdminTokenOverview | null>(null);
  readonly users = signal<AdminTokenUserRow[]>([]);
  readonly loading = signal(false);
  readonly year = signal(new Date().getUTCFullYear());
  readonly limitInput = signal(String(1_000_000));
  readonly limitOutput = signal(String(1_000_000));

  /** Mat-select multiple model (plain array for ngModel). */
  selectedUsers: string[] = [];

  readonly yearOptions = computed(() => {
    const y = new Date().getUTCFullYear();
    return [y, y - 1, y - 2, y - 3];
  });

  readonly heatCells = computed(() => {
    const ov = this.overview();
    if (!ov) return [];
    return this.buildHeatCells(this.year(), ov.daily || {});
  });

  readonly monthRows = computed(() => {
    const ov = this.overview();
    if (!ov?.monthly) return [];
    const y = this.year();
    const prefix = `${y}-`;
    return Object.entries(ov.monthly)
      .filter(([k]) => k.startsWith(prefix))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, v]) => ({
        key,
        ...v,
        combined: v.input_tokens + v.output_tokens,
      }));
  });

  readonly isViewingCurrentUtcYear = computed(
    () => this.year() === new Date().getUTCFullYear()
  );

  ngOnInit(): void {
    this.refreshAll();
  }

  setYear(y: number): void {
    this.year.set(y);
    this.loadOverview();
  }

  refreshAll(): void {
    this.loadOverview();
    this.loadUsers();
  }

  goChat(): void {
    void this.router.navigate(['/chat']);
  }

  applyLimits(): void {
    const emails = this.selectedUsers.slice();
    const inL = Number(this.limitInput().replace(/,/g, ''));
    const outL = Number(this.limitOutput().replace(/,/g, ''));
    if (!emails.length) {
      this.snack.open('Select at least one user email', 'Dismiss', { duration: 4000 });
      return;
    }
    if (!Number.isFinite(inL) || !Number.isFinite(outL) || inL < 0 || outL < 0) {
      this.snack.open('Limits must be non-negative numbers', 'Dismiss', { duration: 4000 });
      return;
    }
    this.adminApi.setLimits(emails, inL, outL).subscribe({
      next: (r) => {
        this.snack.open(`Updated limits for ${r.count} user(s)`, 'Dismiss', { duration: 4000 });
        this.selectedUsers = [];
        this.loadUsers();
      },
      error: (e) => {
        const msg = e?.error?.error ?? e?.message ?? 'Failed to update limits';
        this.snack.open(String(msg), 'Dismiss', { duration: 6000 });
      },
    });
  }

  cellTooltip(cell: HeatCell): string {
    if (!cell.inYear) {
      return `${cell.dateStr}\n(outside selected year)`;
    }
    const ov = this.overview();
    const d = ov?.daily?.[cell.dateStr];
    if (!cell.total) {
      return `${cell.dateStr} UTC\nNo usage recorded`;
    }
    return [
      `${cell.dateStr} UTC`,
      `In: ${d?.input_tokens?.toLocaleString() ?? 0}  Out: ${d?.output_tokens?.toLocaleString() ?? 0}`,
      `Combined: ${cell.total.toLocaleString()} tok (est.)`,
      `Active users: ${d?.users ?? 0}`,
    ].join('\n');
  }

  private loadOverview(): void {
    this.loading.set(true);
    this.loadError.set(null);
    this.adminApi
      .getOverview(this.year())
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: (d) => this.overview.set(d),
        error: (e) => {
          const msg = e?.error?.error ?? e?.message ?? 'Failed to load overview';
          this.loadError.set(String(msg));
          this.overview.set(null);
        },
      });
  }

  private loadUsers(): void {
    this.adminApi.getUsers().subscribe({
      next: (r) => this.users.set(r.users ?? []),
      error: () => this.users.set([]),
    });
  }

  private buildHeatCells(
    year: number,
    daily: Record<string, DailyAggRow>
  ): HeatCell[] {
    const start = new Date(Date.UTC(year, 0, 1));
    start.setUTCDate(start.getUTCDate() - start.getUTCDay());
    const last = new Date(Date.UTC(year, 11, 31));
    const end = new Date(last);
    end.setUTCDate(end.getUTCDate() + (6 - end.getUTCDay()));

    const cells: HeatCell[] = [];
    const cur = new Date(start);
    while (cur <= end) {
      const dateStr = cur.toISOString().slice(0, 10);
      const inYear = cur.getUTCFullYear() === year;
      let total = 0;
      if (inYear) {
        const row = daily[dateStr];
        total = row
          ? row.input_tokens + row.output_tokens
          : 0;
      }
      cells.push({
        dateStr,
        inYear,
        total,
        level: 0,
      });
      cur.setUTCDate(cur.getUTCDate() + 1);
    }

    const inYearTotals = cells.filter((c) => c.inYear && c.total > 0).map((c) => c.total);
    const max = Math.max(...inYearTotals, 1);
    for (const c of cells) {
      if (!c.inYear || c.total <= 0) {
        c.level = 0;
        continue;
      }
      const r = c.total / max;
      if (r <= 0.25) c.level = 1;
      else if (r <= 0.5) c.level = 2;
      else if (r <= 0.75) c.level = 3;
      else c.level = 4;
    }
    return cells;
  }
}
