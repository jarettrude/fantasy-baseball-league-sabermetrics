/**
 * Astro middleware for custom error page handling.
 *
 * Intercepts HTTP responses to provide user-friendly error pages
 * for authentication/authorization failures and 404 errors while
 * preserving JSON API responses for frontend consumption.
 */

import { defineMiddleware } from "astro:middleware";

export const onRequest = defineMiddleware(async (context, next) => {
  const response = await next();
  const isHtml = context.request.headers.get("accept")?.includes("text/html");

  // Rewrite auth errors to custom security page for HTML requests only
  // This preserves JSON API error responses for frontend JavaScript
  if (isHtml && (response.status === 403 || response.status === 401)) {
    return context.rewrite("/403");
  }

  // Handle 404 errors with custom page, excluding the 404 page itself
  // Prevents infinite redirect loops while maintaining consistent UX
  if (
    isHtml &&
    response.status === 404 &&
    !context.url.pathname.endsWith("/404")
  ) {
    return context.rewrite("/404");
  }

  return response;
});
