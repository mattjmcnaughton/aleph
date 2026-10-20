export type InstallContext = {
  userAgent: string;
  standalone: boolean;
  dismissedAt: number | null;
  now: number;
};

function isIPhoneSafari(userAgent: string): boolean {
  return (
    /iPhone/.test(userAgent) &&
    !/iPad/.test(userAgent) &&
    /Safari\//.test(userAgent) &&
    !/CriOS|FxiOS|EdgiOS|OPiOS/.test(userAgent)
  );
}

export function shouldShowInstallCard(ctx: InstallContext): boolean {
  return (
    isIPhoneSafari(ctx.userAgent) &&
    !ctx.standalone &&
    (ctx.dismissedAt === null ||
      !Number.isFinite(ctx.dismissedAt) ||
      ctx.now - ctx.dismissedAt >= 30 * 24 * 60 * 60 * 1000)
  );
}

export function installState(
  ctx: InstallContext & { pushSupported: boolean },
): "home_screen" | "needs_home_screen" | "unsupported" | "browser_ok" {
  if (ctx.standalone) return ctx.pushSupported ? "home_screen" : "unsupported";
  if (isIPhoneSafari(ctx.userAgent)) return "needs_home_screen";
  return ctx.pushSupported ? "browser_ok" : "unsupported";
}

export function isStandalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}
