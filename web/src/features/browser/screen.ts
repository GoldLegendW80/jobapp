/** What the live view needs of noVNC's RFB: an object, so a test can put a fake in its place. */
export interface Screen {
  viewOnly: boolean;
  disconnect(): void;
  focus(): void;
  /** One key pressed and released, by X keysym. */
  typeKey(keysym: number): void;
  /** The X keysym of a character (noVNC's table). */
  keysymOf(character: string): number;
}

export interface ScreenEvents {
  connect(): void;
  /** The server turned this view away (too many open views): the disconnect follows. */
  refused(): void;
  disconnect(): void;
}

export const screenFactory = {
  /**
   * Opens the websocket and draws on a canvas inside `target`, view only. noVNC is 1.4.0, pinned
   * in package.json (vite.config.ts puts it in the vendor chunk with every other package).
   */
  async open(
    target: HTMLElement,
    url: string,
    events: ScreenEvents,
  ): Promise<Screen> {
    const [{ default: RFB }, { default: keysyms }] = await Promise.all([
      import("@novnc/novnc/core/rfb.js"),
      import("@novnc/novnc/core/input/keysymdef.js"),
    ]);
    const rfb = new RFB(target, url, { shared: true, wsProtocols: ["binary"] });
    rfb.viewOnly = true;
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.background = "transparent";
    rfb.addEventListener("connect", () => {
      events.connect();
    });
    rfb.addEventListener("securityfailure", () => {
      events.refused();
    });
    rfb.addEventListener("disconnect", () => {
      events.disconnect();
    });
    return {
      get viewOnly() {
        return rfb.viewOnly;
      },
      set viewOnly(value: boolean) {
        rfb.viewOnly = value;
      },
      disconnect: () => {
        rfb.disconnect();
      },
      focus: () => {
        rfb.focus();
      },
      typeKey: (keysym) => {
        rfb.sendKey(keysym, null);
      },
      keysymOf: (character) => keysyms.lookup(character.codePointAt(0) ?? 0),
    };
  },
};
