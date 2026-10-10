/**
 * Tests only: a stand-in for the browser's EventSource (jsdom has none). Every instance is kept,
 * so a test can drive the stream: open it, emit steps, fail it the way the browser does.
 */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static all: FakeEventSource[] = [];

  url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent<string>) => void>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.all.push(this);
  }

  static get last(): FakeEventSource | undefined {
    return FakeEventSource.all.at(-1);
  }

  static reset() {
    FakeEventSource.all = [];
  }

  addEventListener(name: string, fn: (event: MessageEvent<string>) => void) {
    this.listeners.set(name, fn);
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }

  emit(name: string, data: unknown) {
    this.listeners.get(name)?.({
      data: typeof data === "string" ? data : JSON.stringify(data),
    } as MessageEvent<string>);
  }

  /** The connection dropped: the browser will retry by itself (CONNECTING) or gave up (CLOSED). */
  fail(gaveUp: boolean) {
    this.readyState = gaveUp
      ? FakeEventSource.CLOSED
      : FakeEventSource.CONNECTING;
    this.onerror?.();
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  get closed() {
    return this.readyState === FakeEventSource.CLOSED;
  }
}
