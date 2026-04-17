import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_ROUTE_PREFIXES = ["/auth", "/verify-email"];
const AUTH_TOKEN_COOKIE = "lw_auth_token";
const AUTH_EXP_COOKIE = "lw_auth_exp";

function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
}

function hasValidAuthCookie(request: NextRequest): boolean {
  const token = request.cookies.get(AUTH_TOKEN_COOKIE)?.value;
  const expiryRaw = request.cookies.get(AUTH_EXP_COOKIE)?.value;

  if (!token || !expiryRaw) {
    return false;
  }

  const expiryEpoch = Number(expiryRaw);
  if (!Number.isFinite(expiryEpoch)) {
    return false;
  }

  const nowEpoch = Math.floor(Date.now() / 1000);
  return expiryEpoch > nowEpoch;
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const isPublic = isPublicRoute(pathname);
  const hasAuth = hasValidAuthCookie(request);

  if (!hasAuth && !isPublic) {
    const signInUrl = new URL("/auth", request.url);
    const requestedPath = `${pathname}${search}`;
    if (requestedPath && requestedPath !== "/") {
      signInUrl.searchParams.set("next", requestedPath);
    }
    return NextResponse.redirect(signInUrl);
  }

  if (hasAuth && isPublic) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
