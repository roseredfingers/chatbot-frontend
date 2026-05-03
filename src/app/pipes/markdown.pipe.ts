import { Pipe, PipeTransform, inject } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import DOMPurify from 'dompurify';
import { marked } from 'marked';

marked.use({
  breaks: true,
  gfm: true,
});

@Pipe({
  name: 'markdown',
  standalone: true,
})
export class MarkdownPipe implements PipeTransform {
  private readonly sanitizer = inject(DomSanitizer);

  transform(value: string | null | undefined): SafeHtml {
    if (value == null || value === '') {
      return this.sanitizer.bypassSecurityTrustHtml('');
    }

    const raw = marked.parse(value, { async: false }) as string;
    const clean = DOMPurify.sanitize(raw, {
      USE_PROFILES: { html: true },
    });
    return this.sanitizer.bypassSecurityTrustHtml(clean);
  }
}
