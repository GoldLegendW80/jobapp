// The part of noVNC 1.4.0 (package @novnc/novnc, pinned in package.json) the live view uses. The
// package ships its ES modules without types.
declare module "@novnc/novnc/core/rfb.js" {
  export interface RfbOptions {
    shared?: boolean;
    wsProtocols?: string[];
  }
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, url: string, options?: RfbOptions);
    viewOnly: boolean;
    scaleViewport: boolean;
    clipViewport: boolean;
    resizeSession: boolean;
    focusOnClick: boolean;
    background: string;
    disconnect(): void;
    focus(options?: FocusOptions): void;
    sendKey(keysym: number, code: string | null, down?: boolean): void;
  }
}

declare module "@novnc/novnc/core/input/keysymdef.js" {
  const keysyms: { lookup(codepoint: number): number };
  export default keysyms;
}
