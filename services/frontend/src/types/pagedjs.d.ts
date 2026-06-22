// Minimal ambient types for Paged.js (the package ships no type definitions).
// Only the surface we use — the Previewer.preview() flow — is declared. See
// node_modules/pagedjs/src/polyfill/previewer.js for the source of truth.
declare module "pagedjs" {
  /** The chunker's flow result returned by preview(). `total` = page count. */
  interface PagedFlow {
    total: number;
    pages: unknown[];
    performance: number;
    size: unknown;
  }

  export class Previewer {
    constructor(options?: unknown);
    /**
     * Chunk `content` into real page boxes inside `renderTo`.
     * @param content     DOM node / fragment to paginate (omit to use document.body).
     * @param stylesheets URLs, or raw-CSS maps `{ anyKey: cssText }`.
     * @param renderTo    container element the `.pagedjs_pages` are appended to.
     */
    preview(
      content?: Node | DocumentFragment | null,
      stylesheets?: Array<string | Record<string, string>>,
      renderTo?: Element | null,
    ): Promise<PagedFlow>;
    on(event: string, cb: (...args: unknown[]) => void): void;
  }
}
